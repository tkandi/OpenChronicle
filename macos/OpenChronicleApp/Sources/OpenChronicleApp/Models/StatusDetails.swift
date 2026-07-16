import Foundation

struct StatusDetails: Decodable, Equatable {
  let schemaVersion: Int
  let generatedAt: String
  let version: String
  let root: String
  let daemon: DaemonStatusDetails
  let health: HealthStatusDetails
  let capture: CaptureStatusDetails
  let lastCapture: LastCaptureStatusDetails
  let buffer: BufferStatusDetails
  let sessions: SessionStatusDetails
  let memory: MemoryStatusDetails
  let timeline: TimelineStatusDetails
  var models: [String: ModelDiagnostic]

  enum CodingKeys: String, CodingKey {
    case schemaVersion = "schema_version"
    case generatedAt = "generated_at"
    case version
    case root
    case daemon
    case health
    case capture
    case lastCapture = "last_capture"
    case buffer
    case sessions
    case memory
    case timeline
    case models
  }
}

struct DaemonStatusDetails: Decodable, Equatable {
  let running: Bool
  let pid: Int32?
  let uptime: String
}

struct HealthStatusDetails: Decodable, Equatable {
  let label: String
  let state: String
}

struct CaptureStatusDetails: Decodable, Equatable {
  let state: String
  let paused: Bool
}

struct LastCaptureStatusDetails: Decodable, Equatable {
  let timestamp: String?
  let relative: String
  let app: String?
  let file: String?
}

struct BufferStatusDetails: Decodable, Equatable {
  let count: Int
  let lastFile: String?

  enum CodingKeys: String, CodingKey {
    case count
    case lastFile = "last_file"
  }
}

struct SessionStatusDetails: Decodable, Equatable {
  let total: Int
  let reduced: Int
  let ended: Int
  let failed: Int
}

struct MemoryStatusDetails: Decodable, Equatable {
  let activeFiles: Int
  let dormantFiles: Int
  let entries: Int

  enum CodingKeys: String, CodingKey {
    case activeFiles = "active_files"
    case dormantFiles = "dormant_files"
    case entries
  }
}

struct TimelineStatusDetails: Decodable, Equatable {
  let blocks: Int
  let lastEnd: String?

  enum CodingKeys: String, CodingKey {
    case blocks
    case lastEnd = "last_end"
  }
}

struct ModelDiagnostic: Decodable, Equatable {
  let model: String
  let checked: Bool
  let ok: Bool?
  let latencyMs: Int?
  let error: String?
  let mocked: Bool

  enum CodingKeys: String, CodingKey {
    case model
    case checked
    case ok
    case latencyMs = "latency_ms"
    case error
    case mocked
  }
}
