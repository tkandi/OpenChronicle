import Foundation

struct ModelFailureEvent: Codable, Equatable, Identifiable {
  let schemaVersion: Int
  let id: String
  let timestamp: String
  let stage: String
  let model: String
  let errorType: String
  let message: String

  enum CodingKeys: String, CodingKey {
    case schemaVersion = "schema_version"
    case id
    case timestamp
    case stage
    case model
    case errorType = "error_type"
    case message
  }

  var notificationTitle: String {
    "OpenChronicle: \(stage.capitalized) model failed"
  }

  var notificationBody: String {
    "\(model) · \(errorType): \(message)"
  }
}

/// Incremental JSONL reader for the backend-to-app failure event handoff.
///
/// The reader starts at EOF so installing or relaunching the app never emits a
/// burst of historical alerts. A trailing partial line is retained until the
/// backend finishes its single append write.
struct ModelFailureEventReader {
  let fileURL: URL
  var fileManager: FileManager = .default

  private(set) var offset: UInt64 = 0
  private var partialLine = Data()

  init(fileURL: URL, fileManager: FileManager = .default) {
    self.fileURL = fileURL
    self.fileManager = fileManager
  }

  mutating func skipExistingEvents() {
    offset = fileSize() ?? 0
    partialLine.removeAll(keepingCapacity: false)
  }

  mutating func readNewEvents() throws -> [ModelFailureEvent] {
    guard let size = fileSize() else { return [] }
    if size < offset {
      // Be tolerant of manual cleanup or future log rotation.
      offset = 0
      partialLine.removeAll(keepingCapacity: false)
    }
    guard size > offset else { return [] }

    let handle = try FileHandle(forReadingFrom: fileURL)
    defer { try? handle.close() }
    try handle.seek(toOffset: offset)
    let newData = try handle.readToEnd() ?? Data()
    offset += UInt64(newData.count)

    var combined = partialLine
    combined.append(newData)
    guard let finalNewline = combined.lastIndex(of: 0x0A) else {
      partialLine = combined
      return []
    }

    let afterNewline = combined.index(after: finalNewline)
    let complete = combined[..<afterNewline]
    partialLine = Data(combined[afterNewline...])
    let decoder = JSONDecoder()
    return complete.split(separator: 0x0A).compactMap { line in
      try? decoder.decode(ModelFailureEvent.self, from: Data(line))
    }
  }

  private func fileSize() -> UInt64? {
    guard fileManager.fileExists(atPath: fileURL.path),
      let attributes = try? fileManager.attributesOfItem(atPath: fileURL.path),
      let size = attributes[.size] as? NSNumber
    else {
      return nil
    }
    return size.uint64Value
  }
}
