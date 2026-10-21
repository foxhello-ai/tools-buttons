import Foundation

enum VisibilityMode: String {
    case automatic, shown, hidden
}

struct ProjectBinding {
    let directory: URL
    let projectID: String?

    var preferenceKey: String { projectID ?? Self.canonicalPath(directory.path) }

    static func canonicalPath(_ path: String) -> String {
        URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL.resolvingSymlinksInPath().path
    }
}

// This is an adapter for the installed desktop app's private persisted schema.
// Never substitute active-workspace-roots: it can retain a previous directory.
struct CodexProjectSelection: Decodable {
    struct Selected: Decodable {
        let type: String
        let projectId: String?
    }

    struct LocalProject: Decodable {
        let rootPaths: [String]
    }

    let selected: Selected?
    let projects: [String: LocalProject]?

    enum CodingKeys: String, CodingKey {
        case selected = "selected-project"
        case projects = "local-projects"
    }

    func matches(_ binding: ProjectBinding) -> Bool {
        guard let selected, selected.type == "local", let id = selected.projectId,
              let project = projects?[id] else { return false }
        if let expectedID = binding.projectID, expectedID != id { return false }
        let expectedPath = ProjectBinding.canonicalPath(binding.directory.path)
        return project.rootPaths.contains { ProjectBinding.canonicalPath($0) == expectedPath }
    }
}

struct ProjectVisibility {
    let binding: ProjectBinding
    let defaults: UserDefaults
    private var storageKey: String { "projectVisibilityMode:\(binding.preferenceKey)" }

    var mode: VisibilityMode {
        get { defaults.string(forKey: storageKey).flatMap(VisibilityMode.init(rawValue:)) ?? .automatic }
        nonmutating set { defaults.set(newValue.rawValue, forKey: storageKey) }
    }

    func shouldShow(selection: CodexProjectSelection?, hostIsRunning: Bool) -> Bool {
        switch mode {
        case .shown: return true
        case .hidden: return false
        case .automatic: return hostIsRunning && selection?.matches(binding) == true
        }
    }
}
