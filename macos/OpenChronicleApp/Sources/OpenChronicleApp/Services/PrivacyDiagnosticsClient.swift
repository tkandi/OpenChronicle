import Darwin
import Foundation

enum PrivacyDiagnosticsAction: String, Codable, Equatable, Hashable {
  case subscribe
  case acquireExact = "acquire_exact"
  case moveExact = "move_exact"
  case releaseExact = "release_exact"
}

enum PrivacyDiagnosticsDetail: String, Codable, Equatable {
  case category
  case exact
}

struct PrivacyDiagnosticsRequest: Codable, Equatable {
  let schemaVersion: Int
  let action: PrivacyDiagnosticsAction
  let detail: PrivacyDiagnosticsDetail?
  let pid: Int32?
  let displayID: Int?
  let leaseID: String?

  private init(
    action: PrivacyDiagnosticsAction,
    detail: PrivacyDiagnosticsDetail? = nil,
    pid: Int32? = nil,
    displayID: Int? = nil,
    leaseID: String? = nil
  ) {
    schemaVersion = 1
    self.action = action
    self.detail = detail
    self.pid = pid
    self.displayID = displayID
    self.leaseID = leaseID
  }

  static func subscribe(detail: PrivacyDiagnosticsDetail) -> PrivacyDiagnosticsRequest {
    PrivacyDiagnosticsRequest(action: .subscribe, detail: detail)
  }

  static func acquireExact(pid: Int32, displayID: Int) -> PrivacyDiagnosticsRequest {
    PrivacyDiagnosticsRequest(action: .acquireExact, pid: pid, displayID: displayID)
  }

  static func moveExact(
    pid: Int32,
    leaseID: String,
    displayID: Int
  ) -> PrivacyDiagnosticsRequest {
    PrivacyDiagnosticsRequest(
      action: .moveExact,
      pid: pid,
      displayID: displayID,
      leaseID: leaseID
    )
  }

  static func releaseExact(pid: Int32, leaseID: String) -> PrivacyDiagnosticsRequest {
    PrivacyDiagnosticsRequest(action: .releaseExact, pid: pid, leaseID: leaseID)
  }

  enum CodingKeys: String, CodingKey {
    case schemaVersion = "schema_version"
    case action
    case detail
    case pid
    case displayID = "display_id"
    case leaseID = "lease_id"
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
    guard schemaVersion == 1 else {
      throw DecodingError.dataCorruptedError(
        forKey: .schemaVersion,
        in: container,
        debugDescription: "unsupported_schema"
      )
    }
    action = try container.decode(PrivacyDiagnosticsAction.self, forKey: .action)
    detail = try container.decodeIfPresent(PrivacyDiagnosticsDetail.self, forKey: .detail)
    pid = try container.decodeIfPresent(Int32.self, forKey: .pid)
    displayID = try container.decodeIfPresent(Int.self, forKey: .displayID)
    leaseID = try container.decodeIfPresent(String.self, forKey: .leaseID)
  }

  func encode(to encoder: Encoder) throws {
    var container = encoder.container(keyedBy: CodingKeys.self)
    try container.encode(1, forKey: .schemaVersion)
    try container.encode(action, forKey: .action)
    switch action {
    case .subscribe:
      try container.encode(detail, forKey: .detail)
    case .acquireExact:
      try container.encode(pid, forKey: .pid)
      try container.encode(displayID, forKey: .displayID)
    case .moveExact:
      try container.encode(pid, forKey: .pid)
      try container.encode(leaseID, forKey: .leaseID)
      try container.encode(displayID, forKey: .displayID)
    case .releaseExact:
      try container.encode(pid, forKey: .pid)
      try container.encode(leaseID, forKey: .leaseID)
    }
  }
}

protocol PrivacyDiagnosticsTransport: AnyObject {
  var onMessage: ((ProtectionDiagnosticsWireMessage) -> Void)? { get set }
  var onDisconnect: ((Error?) -> Void)? { get set }
  func connect() throws
  func send(_ request: PrivacyDiagnosticsRequest) throws
  func flushPendingWrites(timeout: TimeInterval) throws
  func close()
}

enum PrivacyDiagnosticsTransportError: String, Error, LocalizedError {
  case alreadyConnected = "already_connected"
  case connectionFailed = "connection_failed"
  case invalidSocketPath = "invalid_socket_path"
  case notConnected = "not_connected"
  case requestTooLarge = "request_too_large"
  case writeQueueFull = "write_queue_full"
  case writeFailed = "write_failed"
  case lineTooLong = "line_too_long"
  case invalidMessage = "invalid_message"

  var errorDescription: String? { rawValue }
}

final class UnixPrivacyDiagnosticsTransport: PrivacyDiagnosticsTransport {
  private static let maximumLineBytes = 64 * 1024
  private static let maximumQueuedMessages = 8
  private static let maximumQueuedBytes = 4 * maximumLineBytes

  private let socketURL: URL
  private let ioQueue = DispatchQueue(label: "openchronicle.privacy-diagnostics.socket")
  private let ioQueueKey = DispatchSpecificKey<Void>()
  private let callbackLock = NSLock()
  private var messageCallback: ((ProtectionDiagnosticsWireMessage) -> Void)?
  private var disconnectCallback: ((Error?) -> Void)?

  private var descriptor: Int32 = -1
  private var readSource: DispatchSourceRead?
  private var received = Data()
  private var outgoing: [Data] = []
  private var outgoingBytes = 0
  private var sendOffset = 0
  private var writeRetryScheduled = false

  var onMessage: ((ProtectionDiagnosticsWireMessage) -> Void)? {
    get { callbackLock.withLock { messageCallback } }
    set { callbackLock.withLock { messageCallback = newValue } }
  }

  var onDisconnect: ((Error?) -> Void)? {
    get { callbackLock.withLock { disconnectCallback } }
    set { callbackLock.withLock { disconnectCallback = newValue } }
  }

  init(socketURL: URL = RuntimePaths.live().privacyDiagnosticsSocket) {
    self.socketURL = socketURL
    ioQueue.setSpecific(key: ioQueueKey, value: ())
  }

  deinit {
    close()
  }

  func connect() throws {
    let socketDescriptor = try Self.openSocket(path: socketURL.path)
    do {
      try ioQueue.sync {
        guard descriptor < 0 else {
          throw PrivacyDiagnosticsTransportError.alreadyConnected
        }
        descriptor = socketDescriptor
        received.removeAll(keepingCapacity: true)
        outgoing.removeAll(keepingCapacity: true)
        outgoingBytes = 0
        sendOffset = 0
        writeRetryScheduled = false

        let source = DispatchSource.makeReadSource(
          fileDescriptor: socketDescriptor,
          queue: ioQueue
        )
        source.setEventHandler { [weak self] in
          self?.drainReads(expectedDescriptor: socketDescriptor)
        }
        readSource = source
        source.resume()
      }
    } catch {
      Darwin.close(socketDescriptor)
      throw error
    }
  }

  func send(_ request: PrivacyDiagnosticsRequest) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    var encoded: Data
    do {
      encoded = try encoder.encode(request)
    } catch {
      throw PrivacyDiagnosticsTransportError.writeFailed
    }
    guard encoded.count <= Self.maximumLineBytes else {
      throw PrivacyDiagnosticsTransportError.requestTooLarge
    }
    encoded.append(0x0A)

    try ioQueue.sync {
      guard descriptor >= 0 else {
        throw PrivacyDiagnosticsTransportError.notConnected
      }
      guard outgoing.count < Self.maximumQueuedMessages,
        outgoingBytes + encoded.count <= Self.maximumQueuedBytes
      else {
        throw PrivacyDiagnosticsTransportError.writeQueueFull
      }
      outgoing.append(encoded)
      outgoingBytes += encoded.count
      do {
        try flushWrites(expectedDescriptor: descriptor)
      } catch {
        terminate(error: PrivacyDiagnosticsTransportError.writeFailed, notify: true)
        throw PrivacyDiagnosticsTransportError.writeFailed
      }
    }
  }

  func flushPendingWrites(timeout: TimeInterval) throws {
    try ioQueue.sync {
      guard descriptor >= 0 else {
        throw PrivacyDiagnosticsTransportError.notConnected
      }
      let expectedDescriptor = descriptor
      let deadline = DispatchTime.now() + max(0.0, timeout)
      while !outgoing.isEmpty {
        try flushWrites(expectedDescriptor: expectedDescriptor)
        guard !outgoing.isEmpty else { return }

        let now = DispatchTime.now().uptimeNanoseconds
        guard now < deadline.uptimeNanoseconds else {
          throw PrivacyDiagnosticsTransportError.writeFailed
        }
        let remaining = deadline.uptimeNanoseconds - now
        let roundedMilliseconds =
          remaining / 1_000_000 + (remaining % 1_000_000 == 0 ? 0 : 1)
        let timeoutMilliseconds = Int32(
          min(UInt64(Int32.max), max(1, roundedMilliseconds))
        )
        var writable = pollfd(
          fd: expectedDescriptor,
          events: Int16(POLLOUT),
          revents: 0
        )
        let result = Darwin.poll(&writable, 1, timeoutMilliseconds)
        if result > 0 {
          continue
        }
        if result < 0, errno == EINTR {
          continue
        }
        throw PrivacyDiagnosticsTransportError.writeFailed
      }
    }
  }

  func close() {
    if DispatchQueue.getSpecific(key: ioQueueKey) != nil {
      terminate(error: nil, notify: false)
      return
    }
    ioQueue.sync {
      terminate(error: nil, notify: false)
    }
  }

  private func drainReads(expectedDescriptor: Int32) {
    guard descriptor == expectedDescriptor else { return }
    var buffer = [UInt8](repeating: 0, count: 8192)
    while descriptor == expectedDescriptor {
      let count = Darwin.recv(expectedDescriptor, &buffer, buffer.count, 0)
      if count > 0 {
        received.append(buffer, count: count)
        guard processReceivedLines() else { return }
      } else if count == 0 {
        terminate(error: nil, notify: true)
        return
      } else if errno == EAGAIN || errno == EWOULDBLOCK {
        return
      } else if errno == EINTR {
        continue
      } else {
        terminate(error: PrivacyDiagnosticsTransportError.connectionFailed, notify: true)
        return
      }
    }
  }

  private func processReceivedLines() -> Bool {
    while let newline = received.firstIndex(of: 0x0A) {
      let line = Data(received[..<newline])
      received.removeSubrange(...newline)
      guard line.count <= Self.maximumLineBytes else {
        terminate(error: PrivacyDiagnosticsTransportError.lineTooLong, notify: true)
        return false
      }
      do {
        let message = try JSONDecoder().decode(
          ProtectionDiagnosticsWireMessage.self,
          from: line
        )
        deliver(message)
      } catch {
        terminate(error: PrivacyDiagnosticsTransportError.invalidMessage, notify: true)
        return false
      }
    }
    if received.count > Self.maximumLineBytes {
      terminate(error: PrivacyDiagnosticsTransportError.lineTooLong, notify: true)
      return false
    }
    return true
  }

  private func flushWrites(expectedDescriptor: Int32) throws {
    guard descriptor == expectedDescriptor else {
      throw PrivacyDiagnosticsTransportError.notConnected
    }
    while let message = outgoing.first {
      let sent = message.withUnsafeBytes { bytes -> Int in
        guard let baseAddress = bytes.baseAddress else { return 0 }
        return Darwin.send(
          expectedDescriptor,
          baseAddress.advanced(by: sendOffset),
          message.count - sendOffset,
          0
        )
      }
      if sent > 0 {
        sendOffset += sent
        if sendOffset == message.count {
          outgoing.removeFirst()
          outgoingBytes -= message.count
          sendOffset = 0
        }
      } else if sent < 0, errno == EAGAIN || errno == EWOULDBLOCK {
        scheduleWriteRetry(expectedDescriptor: expectedDescriptor)
        return
      } else if sent < 0, errno == EINTR {
        continue
      } else {
        throw PrivacyDiagnosticsTransportError.writeFailed
      }
    }
  }

  private func scheduleWriteRetry(expectedDescriptor: Int32) {
    guard !writeRetryScheduled else { return }
    writeRetryScheduled = true
    ioQueue.asyncAfter(deadline: .now() + .milliseconds(10)) { [weak self] in
      guard let self else { return }
      self.writeRetryScheduled = false
      guard self.descriptor == expectedDescriptor else { return }
      do {
        try self.flushWrites(expectedDescriptor: expectedDescriptor)
      } catch {
        self.terminate(error: PrivacyDiagnosticsTransportError.writeFailed, notify: true)
      }
    }
  }

  private func deliver(_ message: ProtectionDiagnosticsWireMessage) {
    guard let callback = callbackLock.withLock({ messageCallback }) else { return }
    DispatchQueue.main.async {
      callback(message)
    }
  }

  private func terminate(error: Error?, notify: Bool) {
    guard descriptor >= 0 else { return }
    let oldDescriptor = descriptor
    descriptor = -1
    readSource?.cancel()
    readSource = nil
    Darwin.shutdown(oldDescriptor, SHUT_RDWR)
    Darwin.close(oldDescriptor)
    received.removeAll(keepingCapacity: false)
    outgoing.removeAll(keepingCapacity: false)
    outgoingBytes = 0
    sendOffset = 0
    writeRetryScheduled = false

    guard notify, let callback = callbackLock.withLock({ disconnectCallback }) else { return }
    DispatchQueue.main.async {
      callback(error)
    }
  }

  private static func openSocket(path: String) throws -> Int32 {
    let pathBytes = Array(path.utf8CString)
    var address = sockaddr_un()
    guard pathBytes.count <= MemoryLayout.size(ofValue: address.sun_path) else {
      throw PrivacyDiagnosticsTransportError.invalidSocketPath
    }

    let socketDescriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
    guard socketDescriptor >= 0 else {
      throw PrivacyDiagnosticsTransportError.connectionFailed
    }

    address.sun_family = sa_family_t(AF_UNIX)
    address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
    withUnsafeMutablePointer(to: &address.sun_path.0) { destination in
      pathBytes.withUnsafeBufferPointer { source in
        _ = memcpy(destination, source.baseAddress, pathBytes.count)
      }
    }

    let connected = withUnsafePointer(to: &address) { pointer in
      pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
        Darwin.connect(
          socketDescriptor,
          $0,
          socklen_t(MemoryLayout<sockaddr_un>.size)
        )
      }
    }
    guard connected == 0 else {
      Darwin.close(socketDescriptor)
      throw PrivacyDiagnosticsTransportError.connectionFailed
    }

    var noSignal: Int32 = 1
    _ = Darwin.setsockopt(
      socketDescriptor,
      SOL_SOCKET,
      SO_NOSIGPIPE,
      &noSignal,
      socklen_t(MemoryLayout<Int32>.size)
    )
    let currentFlags = Darwin.fcntl(socketDescriptor, F_GETFL)
    guard currentFlags >= 0,
      Darwin.fcntl(socketDescriptor, F_SETFL, currentFlags | O_NONBLOCK) == 0
    else {
      Darwin.close(socketDescriptor)
      throw PrivacyDiagnosticsTransportError.connectionFailed
    }
    return socketDescriptor
  }
}

private extension NSLock {
  func withLock<Value>(_ operation: () -> Value) -> Value {
    lock()
    defer { unlock() }
    return operation()
  }
}
