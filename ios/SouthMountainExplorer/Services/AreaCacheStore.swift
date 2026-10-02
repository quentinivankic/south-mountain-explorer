import Foundation

enum AreaCacheStoreFailure: String, Equatable, Sendable {
    case invalidPayload
    case stagingWriteFailed
    case stagingVerificationFailed
    case backupWriteFailed
    case promotionFailed
    case finalVerificationFailed
    case rollbackFailed
}

struct AreaCacheStoreReceipt: Equatable, Sendable {
    let areaID: String
    let failure: AreaCacheStoreFailure?

    var succeeded: Bool { failure == nil }
}

/// Owns verified durable area geometry. Replacement bytes are validated in a
/// same-directory staging file before an atomic promotion. If final read-back
/// verification fails, a verified staging copy of the prior valid bytes is
/// atomically restored.
struct AreaCacheStore {
    struct IO {
        var read: (URL) throws -> Data
        var writeStaged: (Data, URL) throws -> Void
        var promote: (URL, URL) throws -> Void
        var remove: (URL) throws -> Void

        static func live(fileManager: FileManager = .default) -> IO {
            IO(
                read: { try Data(contentsOf: $0) },
                writeStaged: { data, url in
                    try data.write(to: url, options: .atomic)
                },
                promote: { stagedURL, destinationURL in
                    if fileManager.fileExists(atPath: destinationURL.path) {
                        _ = try fileManager.replaceItemAt(
                            destinationURL,
                            withItemAt: stagedURL,
                            backupItemName: nil
                        )
                    } else {
                        try fileManager.moveItem(at: stagedURL, to: destinationURL)
                    }
                },
                remove: { try fileManager.removeItem(at: $0) }
            )
        }
    }

    struct Entry {
        let id: String
        let area: Area
        let sizeBytes: Int
    }

    let cacheDirectory: URL
    private let fileManager: FileManager
    private let io: IO

    init(
        cacheDirectory: URL,
        fileManager: FileManager = .default,
        io: IO? = nil
    ) {
        self.cacheDirectory = cacheDirectory
        self.fileManager = fileManager
        self.io = io ?? .live(fileManager: fileManager)
        try? fileManager.createDirectory(
            at: cacheDirectory,
            withIntermediateDirectories: true
        )
    }

    func fileURL(for areaID: String) -> URL {
        cacheDirectory.appendingPathComponent("\(areaID).json")
    }

    func validArea(id: String) -> Area? {
        guard let data = try? io.read(fileURL(for: id)) else { return nil }
        return Self.validatedArea(from: data, expectedID: id)
    }

    func validBytes(id: String) -> Data? {
        guard let data = try? io.read(fileURL(for: id)),
              Self.validatedArea(from: data, expectedID: id) != nil
        else { return nil }
        return data
    }

    func store(_ area: Area, expectedID: String? = nil) -> AreaCacheStoreReceipt {
        guard let data = try? JSONEncoder().encode(area) else {
            return AreaCacheStoreReceipt(areaID: expectedID ?? area.id, failure: .invalidPayload)
        }
        return store(data, expectedID: expectedID ?? area.id)
    }

    func store(_ data: Data, expectedID: String) -> AreaCacheStoreReceipt {
        guard Self.validatedArea(from: data, expectedID: expectedID) != nil else {
            return AreaCacheStoreReceipt(areaID: expectedID, failure: .invalidPayload)
        }

        let destinationURL = fileURL(for: expectedID)
        do {
            try fileManager.createDirectory(
                at: cacheDirectory,
                withIntermediateDirectories: true
            )
        } catch {
            return AreaCacheStoreReceipt(areaID: expectedID, failure: .stagingWriteFailed)
        }
        let token = UUID().uuidString
        let stagedURL = cacheDirectory.appendingPathComponent(".\(expectedID).\(token).stage")
        let backupURL = cacheDirectory.appendingPathComponent(".\(expectedID).\(token).backup")
        defer {
            try? io.remove(stagedURL)
            try? io.remove(backupURL)
        }

        do {
            try io.writeStaged(data, stagedURL)
        } catch {
            return AreaCacheStoreReceipt(areaID: expectedID, failure: .stagingWriteFailed)
        }
        guard let stagedBytes = try? io.read(stagedURL),
              stagedBytes == data,
              Self.validatedArea(from: stagedBytes, expectedID: expectedID) != nil
        else {
            return AreaCacheStoreReceipt(areaID: expectedID, failure: .stagingVerificationFailed)
        }

        let priorBytes = validBytes(id: expectedID)
        if let priorBytes {
            do {
                try io.writeStaged(priorBytes, backupURL)
            } catch {
                return AreaCacheStoreReceipt(areaID: expectedID, failure: .backupWriteFailed)
            }
            guard let backupBytes = try? io.read(backupURL), backupBytes == priorBytes else {
                return AreaCacheStoreReceipt(areaID: expectedID, failure: .backupWriteFailed)
            }
        }

        do {
            try io.promote(stagedURL, destinationURL)
        } catch {
            return AreaCacheStoreReceipt(areaID: expectedID, failure: .promotionFailed)
        }

        if let finalBytes = try? io.read(destinationURL),
           finalBytes == data,
           Self.validatedArea(from: finalBytes, expectedID: expectedID) != nil {
            return AreaCacheStoreReceipt(areaID: expectedID, failure: nil)
        }

        do {
            if priorBytes != nil {
                try io.promote(backupURL, destinationURL)
                guard validBytes(id: expectedID) == priorBytes else {
                    return AreaCacheStoreReceipt(areaID: expectedID, failure: .rollbackFailed)
                }
            } else {
                try io.remove(destinationURL)
            }
        } catch {
            return AreaCacheStoreReceipt(areaID: expectedID, failure: .rollbackFailed)
        }
        return AreaCacheStoreReceipt(areaID: expectedID, failure: .finalVerificationFailed)
    }

    func entries() -> [Entry] {
        guard let files = try? fileManager.contentsOfDirectory(
            at: cacheDirectory,
            includingPropertiesForKeys: [.fileSizeKey]
        ) else { return [] }

        return files.compactMap { file in
            guard file.pathExtension == "json" else { return nil }
            let id = file.deletingPathExtension().lastPathComponent
            guard id != "index-v2", id != "summaries-v2",
                  let data = try? io.read(file),
                  let area = Self.validatedArea(from: data, expectedID: id)
            else { return nil }
            return Entry(id: id, area: area, sizeBytes: data.count)
        }
    }

    static func validatedArea(from data: Data, expectedID: String) -> Area? {
        guard !data.isEmpty,
              let area = try? JSONDecoder().decode(Area.self, from: data),
              area.id == expectedID,
              area.trails.contains(where: { trail in
                  trail.segments.contains(where: { segment in
                      segment.count >= 2 && segment.allSatisfy { point in
                          point.count >= 2 && point[0].isFinite && point[1].isFinite
                      }
                  })
              })
        else { return nil }
        return area
    }
}
