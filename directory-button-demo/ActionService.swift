import Foundation

struct ActionDefinition: Decodable, Equatable {
    let id: String
    let icon: String?
    let tooltip: String
    struct Parameter: Decodable, Equatable { let name: String }
    let parameters: [Parameter]?
    let previewCommand: [String]?
    var requiresConfirmation: Bool { !(parameters ?? []).isEmpty || previewCommand != nil }
}

struct ActionRun: Decodable {
    let id: String
    let actionId: String
    let status: String
    var isActive: Bool { !["succeeded", "failed", "cancelled", "timed_out", "interrupted"].contains(status) }
}

struct ActionProject: Decodable {
    struct Project: Decodable { let name: String }
    let project: Project
    let directory: String
    let actions: [ActionDefinition]
    let runs: [ActionRun]
}

private struct ServiceDescriptor: Decodable {
    let version: Int
    let url: URL
    let directory: String
    let token: String
}

struct ServiceError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

// All callbacks and mutable state belong to the main queue. Commands remain in Python.
final class ActionService {
    let directory: URL
    private let script: URL
    private let python: String?
    private var descriptor: ServiceDescriptor?
    private var child: Process?
    private var connecting = false
    private var stopped = false
    var baseURL: URL? { descriptor?.url }
    var ownsService: Bool { child?.isRunning == true }

    init(directory: URL, script: URL, python: String?) {
        self.directory = directory.standardizedFileURL.resolvingSymlinksInPath()
        self.script = script
        self.python = python
    }

    func connect(_ completion: @escaping (Result<ActionProject, Error>) -> Void) {
        guard !connecting, !stopped else { return }
        connecting = true
        discover { result in
            if case .success = result { self.connecting = false; completion(result); return }
            if self.child?.isRunning != true {
                do { try self.launch() }
                catch { self.connecting = false; completion(.failure(error)); return }
            }
            self.waitForService(attempts: 80, completion: completion)
        }
    }

    private func discover(_ completion: @escaping (Result<ActionProject, Error>) -> Void) {
        do {
            let file = directory.appendingPathComponent(".project-actions/.runtime.json")
            let candidate = try JSONDecoder().decode(ServiceDescriptor.self, from: Data(contentsOf: file))
            guard candidate.version == 1, candidate.url.scheme == "http", candidate.url.host == "127.0.0.1",
                  candidate.url.port != nil, candidate.url.user == nil, candidate.url.password == nil,
                  candidate.url.query == nil, candidate.url.fragment == nil,
                  ["", "/"].contains(candidate.url.path), !candidate.token.isEmpty,
                  ProjectBinding.canonicalPath(candidate.directory) == directory.path else {
                throw ServiceError(message: "执行服务与当前项目不匹配")
            }
            descriptor = candidate
            fetchProject { result in
                if case .failure = result { self.descriptor = nil }
                completion(result)
            }
        } catch { descriptor = nil; completion(.failure(error)) }
    }

    private func launch() throws {
        guard FileManager.default.fileExists(atPath: directory.appendingPathComponent(".project-actions/actions.json").path) else {
            throw ServiceError(message: "项目缺少 .project-actions/actions.json，请先用 project_actions.py init 初始化")
        }
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw ServiceError(message: "应用缺少执行服务：\(script.path)")
        }
        let process = Process()
        if let python, FileManager.default.isExecutableFile(atPath: python) {
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = ["-B", script.path, "serve", "--project", directory.path]
        } else {
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["python3", "-B", script.path, "serve", "--project", directory.path]
        }
        var serviceEnvironment = ProcessInfo.processInfo.environment
        serviceEnvironment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = serviceEnvironment
        process.currentDirectoryURL = directory
        let log = directory.appendingPathComponent(".project-actions/.runtime-service.log")
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        let output = try FileHandle(forWritingTo: log)
        try output.truncate(atOffset: 0)
        process.standardOutput = output
        process.standardError = output
        process.standardInput = FileHandle.nullDevice
        try process.run()
        try? output.close()
        child = process
    }

    private func waitForService(attempts: Int, completion: @escaping (Result<ActionProject, Error>) -> Void) {
        guard !stopped else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            self.discover { result in
                if case .success = result { self.connecting = false; completion(result); return }
                guard attempts > 0, self.child?.isRunning == true else {
                    self.connecting = false
                    completion(.failure(ServiceError(message: "执行服务未能启动；请检查 Python 3.9+ 和 .project-actions/.runtime-service.log")))
                    return
                }
                self.waitForService(attempts: attempts - 1, completion: completion)
            }
        }
    }

    func fetchProject(_ completion: @escaping (Result<ActionProject, Error>) -> Void) {
        request("api/project", as: ActionProject.self) { result in
            completion(result.flatMap { project in
                guard ProjectBinding.canonicalPath(project.directory) == self.directory.path else {
                    return .failure(ServiceError(message: "执行服务返回了其他项目，已拒绝连接"))
                }
                return .success(project)
            })
        }
    }

    func start(_ actionID: String, completion: @escaping (Result<ActionRun, Error>) -> Void) {
        request("api/runs", body: ["actionId": actionID], as: ActionRun.self, completion: completion)
    }

    private func request<T: Decodable>(_ path: String, body: [String: String]? = nil, as type: T.Type,
                                       completion: @escaping (Result<T, Error>) -> Void) {
        guard let descriptor else { completion(.failure(ServiceError(message: "执行服务尚未连接"))); return }
        var request = URLRequest(url: descriptor.url.appendingPathComponent(path))
        request.timeoutInterval = 3
        request.setValue("Bearer \(descriptor.token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.httpMethod = "POST"
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        URLSession.shared.dataTask(with: request) { data, response, error in
            let result: Result<T, Error>
            do {
                if let error { throw error }
                guard let data, let http = response as? HTTPURLResponse else {
                    throw ServiceError(message: "执行服务没有返回响应")
                }
                guard (200..<300).contains(http.statusCode) else {
                    let payload = try? JSONSerialization.jsonObject(with: data) as? [String: String]
                    throw ServiceError(message: payload?["error"] ?? "执行服务错误 \(http.statusCode)")
                }
                result = .success(try JSONDecoder().decode(type, from: data))
            } catch { result = .failure(error) }
            DispatchQueue.main.async { if !self.stopped { completion(result) } }
        }.resume()
    }

    func shutdown() {
        stopped = true
        // An independently started web service belongs to its caller.
        if let child, child.isRunning { child.terminate() }
    }
}
