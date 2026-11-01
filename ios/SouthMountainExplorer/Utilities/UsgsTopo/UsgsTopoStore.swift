import Foundation
import Network
import MapKit
import UIKit

// SPIKE (debug-only). See UsgsTopoMath.swift for removal notes.

enum UsgsTopoKeys {
    static let mode = "usgsTopoMode"
    static let replaceApple = "usgsTopoReplaceApple"   // default true
    static let changed = Notification.Name("UsgsTopoChanged")
    static var currentMode: UsgsTopoMode {
        UsgsTopoMode(rawValue: UserDefaults.standard.string(forKey: mode) ?? "") ?? .off
    }
    static var replaceAppleContent: Bool {
        UserDefaults.standard.object(forKey: replaceApple) == nil ? true : UserDefaults.standard.bool(forKey: replaceApple)
    }
}

/// Local tile directory + stats. Pure file IO, no network.
enum UsgsTopoFiles {
    static var root: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("UsgsTopo", isDirectory: true)
    }
    static func url(for t: UsgsTopoTile) -> URL { root.appendingPathComponent(UsgsTopoMath.relativePath(t)) }

    static func stats() -> (count: Int, bytes: Int64) {
        guard let e = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey]) else { return (0, 0) }
        var n = 0; var b: Int64 = 0
        for case let u as URL in e {
            let v = try? u.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
            if v?.isRegularFile == true { n += 1; b += Int64(v?.fileSize ?? 0) }
        }
        return (n, b)
    }
}

/// Shared NWPathMonitor; posts `UsgsTopoKeys.changed` on every path change.
final class UsgsTopoReachability: @unchecked Sendable {
    static let shared = UsgsTopoReachability()
    private let monitor = NWPathMonitor()
    private let lock = NSLock()
    private var _satisfied = true
    var isSatisfied: Bool { lock.lock(); defer { lock.unlock() }; return _satisfied }
    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            guard let self else { return }
            self.lock.lock(); self._satisfied = (path.status == .satisfied); self.lock.unlock()
            DispatchQueue.main.async { NotificationCenter.default.post(name: UsgsTopoKeys.changed, object: nil) }
        }
        monitor.start(queue: DispatchQueue(label: "usgs-topo-path"))
    }
    func start() {}  // touching `shared` starts the monitor
}

@MainActor
final class UsgsTopoDownloader: ObservableObject {
    @Published var done = 0
    @Published var total = 0
    @Published var failed = 0
    @Published var running = false
    @Published var statsCount = 0
    @Published var statsBytes: Int64 = 0
    @Published var lastMessage = ""
    private var task: Task<Void, Never>?

    init() { refreshStats() }

    func refreshStats() {
        let s = UsgsTopoFiles.stats()
        statsCount = s.count; statsBytes = s.bytes
    }

    func download() {
        guard !running else { return }
        let tiles = UsgsTopoMath.southMountainTiles()
        total = tiles.count; done = 0; failed = 0; running = true; lastMessage = ""
        let started = Date()
        task = Task { [weak self] in
            let session = URLSession(configuration: {
                let c = URLSessionConfiguration.ephemeral
                c.httpAdditionalHeaders = ["User-Agent": "TrekDex/1.0 (iOS; USGS topo offline spike; hello@trekdex.app)"]
                c.timeoutIntervalForRequest = 30
                return c
            }())
            var idx = 0
            await withTaskGroup(of: Bool.self) { group in
                func add() {
                    guard idx < tiles.count else { return }
                    let t = tiles[idx]; idx += 1
                    group.addTask { await Self.fetch(t, session: session) }
                }
                for _ in 0..<4 { add() }
                for await ok in group {
                    guard let self else { return }
                    await MainActor.run { self.done += 1; if !ok { self.failed += 1 } }
                    if Task.isCancelled { group.cancelAll(); continue }
                    add()
                }
            }
            guard let self else { return }
            self.running = false
            self.refreshStats()
            self.lastMessage = String(format: "Done in %.0fs: %d/%d tiles requested, %d failed/missing", Date().timeIntervalSince(started), self.done, self.total, self.failed)
            NSLog("USGSTOPO %@ files=%d bytes=%lld", self.lastMessage, self.statsCount, self.statsBytes)
            NotificationCenter.default.post(name: UsgsTopoKeys.changed, object: nil)
        }
    }

    func cancel() { task?.cancel() }

    func remove() {
        task?.cancel()
        try? FileManager.default.removeItem(at: UsgsTopoFiles.root)
        refreshStats()
        lastMessage = "Removed"
        NotificationCenter.default.post(name: UsgsTopoKeys.changed, object: nil)
    }

    /// One try plus one retry. Skips tiles already on disk. 404 = no tile (not retried).
    nonisolated static func fetch(_ t: UsgsTopoTile, session: URLSession) async -> Bool {
        let dest = UsgsTopoFiles.url(for: t)
        if FileManager.default.fileExists(atPath: dest.path) { return true }
        for attempt in 0..<2 {
            do {
                let (data, resp) = try await session.data(from: UsgsTopoMath.remoteURL(t))
                let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                if code == 200, !data.isEmpty {
                    try FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try data.write(to: dest, options: .atomic)
                    return true
                }
                if code == 404 { return false }
            } catch { /* retry once */ }
            if attempt == 0 { try? await Task.sleep(nanoseconds: 500_000_000) }
        }
        return false
    }
}

/// Serves ONLY local files. Never touches the network.
final class UsgsTopoLocalOverlay: MKTileOverlay {
    private static let transparentPNG: Data = {
        UIGraphicsImageRenderer(size: CGSize(width: 1, height: 1)).pngData { _ in }
    }()

    init(replaceContent: Bool) {
        super.init(urlTemplate: nil)
        tileSize = CGSize(width: 256, height: 256)
        minimumZ = UsgsTopoMath.minZoom
        maximumZ = UsgsTopoMath.maxZoom
        canReplaceMapContent = replaceContent
        isGeometryFlipped = false
    }

    override func loadTile(at path: MKTileOverlayPath, result: @escaping (Data?, Error?) -> Void) {
        let t = UsgsTopoTile(z: path.z, x: path.x, y: path.y)
        if let d = try? Data(contentsOf: UsgsTopoFiles.url(for: t)) {
            result(d, nil)
        } else {
            result(Self.transparentPNG, nil)
        }
    }
}

/// Owns the overlay + attribution label for one MKMapView. Isolated from camera/selection code.
@MainActor
final class UsgsTopoController: NSObject {
    private weak var mapView: MKMapView?
    private var overlay: UsgsTopoLocalOverlay?
    private let label = UILabel()
    nonisolated(unsafe) private var observers: [NSObjectProtocol] = []

    init(mapView: MKMapView) {
        self.mapView = mapView
        super.init()
        _ = UsgsTopoReachability.shared
        NSLog("USGSTOPO controller init args=%@", ProcessInfo.processInfo.arguments.joined(separator: " "))
        label.text = UsgsTopoMath.attribution
        label.font = .systemFont(ofSize: 10)
        label.textColor = .darkGray
        label.backgroundColor = UIColor.white.withAlphaComponent(0.75)
        label.numberOfLines = 1
        label.isHidden = true
        label.isUserInteractionEnabled = false
        label.accessibilityIdentifier = "usgsTopoAttribution"
        label.translatesAutoresizingMaskIntoConstraints = false
        mapView.addSubview(label)
        NSLayoutConstraint.activate([
            // Top-center under the status bar: the bottom is covered by the area sheet.
            label.centerXAnchor.constraint(equalTo: mapView.centerXAnchor),
            label.topAnchor.constraint(equalTo: mapView.safeAreaLayoutGuide.topAnchor, constant: 2),
        ])
        let nc = NotificationCenter.default
        for name in [UsgsTopoKeys.changed, UserDefaults.didChangeNotification] {
            observers.append(nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.refresh() }
            })
        }
        refresh()
        // Spike-only: launch with `--usgs-topo-autodownload` to start the download without UI taps.
        if ProcessInfo.processInfo.arguments.contains("--usgs-topo-autodownload") {
            NSLog("USGSTOPO autodownload start")
            autoDownloader.download()
        }
    }

    private let autoDownloader = UsgsTopoDownloader()

    deinit { observers.forEach(NotificationCenter.default.removeObserver) }

    func refresh() {
        guard let mv = mapView else { return }
        let show = UsgsTopoMath.shouldShow(mode: UsgsTopoKeys.currentMode,
                                           pathSatisfied: UsgsTopoReachability.shared.isSatisfied,
                                           hasTiles: UsgsTopoFiles.stats().count > 0)
        let replace = UsgsTopoKeys.replaceAppleContent
        if show {
            if let o = overlay, o.canReplaceMapContent == replace { label.isHidden = false; return }
            if let o = overlay { mv.removeOverlay(o) }
            let o = UsgsTopoLocalOverlay(replaceContent: replace)
            overlay = o
            mv.insertOverlay(o, at: 0, level: .aboveRoads)  // bottom of overlay stack, under trails
            label.isHidden = false
        } else {
            if let o = overlay { mv.removeOverlay(o); overlay = nil }
            label.isHidden = true
        }
    }
}
