import Foundation

enum CapturePauseMode: String, Codable {
  case timed
  case indefinite
}

struct CapturePauseState: Codable, Equatable, Identifiable {
  static let warningLeadTime: TimeInterval = 60
  static let heartbeatMaxAge: TimeInterval = 90
  static let heartbeatInterval: TimeInterval = 30
  static let firstIndefiniteReminderDelay: TimeInterval = 60 * 60
  static let repeatedIndefiniteReminderDelay: TimeInterval = 2 * 60 * 60

  let schemaVersion: Int
  let id: String
  var mode: CapturePauseMode
  let startedAt: Date
  var resumeAt: Date?
  var resumeArmedAt: Date?
  var appHeartbeatAt: Date?
  var lastReminderAt: Date?

  enum CodingKeys: String, CodingKey {
    case schemaVersion = "schema_version"
    case id
    case mode
    case startedAt = "started_at"
    case resumeAt = "resume_at"
    case resumeArmedAt = "resume_armed_at"
    case appHeartbeatAt = "app_heartbeat_at"
    case lastReminderAt = "last_reminder_at"
  }

  static func timed(duration: TimeInterval, now: Date = Date()) -> CapturePauseState {
    CapturePauseState(
      schemaVersion: 1,
      id: UUID().uuidString,
      mode: .timed,
      startedAt: now,
      resumeAt: now.addingTimeInterval(duration),
      resumeArmedAt: nil,
      appHeartbeatAt: now,
      lastReminderAt: nil
    )
  }

  static func indefinite(now: Date = Date()) -> CapturePauseState {
    CapturePauseState(
      schemaVersion: 1,
      id: UUID().uuidString,
      mode: .indefinite,
      startedAt: now,
      resumeAt: nil,
      resumeArmedAt: nil,
      appHeartbeatAt: now,
      lastReminderAt: nil
    )
  }

  var effectiveResumeAt: Date? {
    guard let resumeAt, let resumeArmedAt else { return nil }
    return max(resumeAt, resumeArmedAt.addingTimeInterval(Self.warningLeadTime))
  }

  func heartbeatIsFresh(at now: Date) -> Bool {
    guard let appHeartbeatAt else { return false }
    return now.timeIntervalSince(appHeartbeatAt) <= Self.heartbeatMaxAge
  }

  func needsWakeRearm(at now: Date) -> Bool {
    guard mode == .timed, resumeArmedAt != nil, let effectiveResumeAt else {
      return false
    }
    return now >= effectiveResumeAt && !heartbeatIsFresh(at: now)
  }

  var nextIndefiniteReminderAt: Date? {
    guard mode == .indefinite else { return nil }
    if let lastReminderAt {
      return lastReminderAt.addingTimeInterval(Self.repeatedIndefiniteReminderDelay)
    }
    return startedAt.addingTimeInterval(Self.firstIndefiniteReminderDelay)
  }
}

struct CapturePauseStateStore {
  let fileURL: URL
  var fileManager: FileManager = .default

  func load(now: Date = Date()) -> CapturePauseState? {
    guard fileManager.fileExists(atPath: fileURL.path) else { return nil }
    let attributes = try? fileManager.attributesOfItem(atPath: fileURL.path)
    let modificationDate = attributes?[.modificationDate] as? Date
    guard let data = try? Data(contentsOf: fileURL) else {
      let startedAt = modificationDate ?? now
      return Self.legacyState(startedAt: startedAt)
    }
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    if let state = try? decoder.decode(CapturePauseState.self, from: data) {
      return state
    }

    // Legacy CLI and app versions stored only an ISO-8601 timestamp. Treat
    // those and malformed files as indefinite pauses to fail closed.
    let legacyText = String(data: data, encoding: .utf8)?
      .trimmingCharacters(in: .whitespacesAndNewlines)
    let startedAt = legacyText.flatMap(Self.parseISO8601) ?? modificationDate ?? now
    return Self.legacyState(startedAt: startedAt)
  }

  private static func legacyState(startedAt: Date) -> CapturePauseState {
    CapturePauseState(
      schemaVersion: 1,
      id: "legacy-\(Int(startedAt.timeIntervalSince1970))",
      mode: .indefinite,
      startedAt: startedAt,
      resumeAt: nil,
      resumeArmedAt: nil,
      appHeartbeatAt: nil,
      lastReminderAt: nil
    )
  }

  func save(_ state: CapturePauseState) throws {
    try fileManager.createDirectory(
      at: fileURL.deletingLastPathComponent(),
      withIntermediateDirectories: true
    )
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    encoder.outputFormatting = [.sortedKeys]
    let data = try encoder.encode(state)
    try data.write(to: fileURL, options: .atomic)
  }

  func clear() throws {
    guard fileManager.fileExists(atPath: fileURL.path) else { return }
    try fileManager.removeItem(at: fileURL)
  }

  private static func parseISO8601(_ value: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    if let date = formatter.date(from: value) {
      return date
    }
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.date(from: value)
  }
}
