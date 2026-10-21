import AppKit

struct ButtonPlacement {
    let binding: ProjectBinding
    let defaults: UserDefaults
    private var storageKey: String { "projectButtonPosition:\(binding.preferenceKey)" }

    var origin: NSPoint? {
        get {
            guard let stored = defaults.dictionary(forKey: storageKey),
                  let x = stored["x"] as? Double, let y = stored["y"] as? Double,
                  x.isFinite, y.isFinite else { return nil }
            return NSPoint(x: x, y: y)
        }
        nonmutating set {
            if let point = newValue, point.x.isFinite, point.y.isFinite {
                defaults.set(["x": Double(point.x), "y": Double(point.y)], forKey: storageKey)
            } else {
                defaults.removeObject(forKey: storageKey)
            }
        }
    }

    // Pick the closest position that keeps the whole button window on a screen.
    // Screen origins may be negative on a desktop with multiple displays.
    static func constrained(_ origin: NSPoint, size: NSSize, screens: [NSRect]) -> NSPoint {
        screens.map { screen in
            NSPoint(x: max(screen.minX, min(origin.x, screen.maxX - size.width)),
                    y: max(screen.minY, min(origin.y, screen.maxY - size.height)))
        }.min {
            hypot($0.x - origin.x, $0.y - origin.y) < hypot($1.x - origin.x, $1.y - origin.y)
        } ?? origin
    }
}
