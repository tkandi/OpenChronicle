import Foundation
import XCTest

@testable import OpenChronicleApp

final class ModelFailureNotificationTests: XCTestCase {
  private var root: URL!
  private var eventsFile: URL!

  override func setUpWithError() throws {
    root = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let paths = RuntimePaths(root: root)
    try FileManager.default.createDirectory(
      at: paths.eventsDirectory,
      withIntermediateDirectories: true
    )
    eventsFile = paths.modelFailureEvents
  }

  override func tearDownWithError() throws {
    try? FileManager.default.removeItem(at: root)
  }

  func testReaderSkipsHistoryAndReturnsOnlyAppendedEvents() throws {
    try append(event(id: "old", stage: "timeline"), newline: true)
    var reader = ModelFailureEventReader(fileURL: eventsFile)
    reader.skipExistingEvents()

    try append(event(id: "new", stage: "classifier"), newline: true)
    let events = try reader.readNewEvents()

    XCTAssertEqual(events.map(\.id), ["new"])
    XCTAssertEqual(events.first?.stage, "classifier")
  }

  func testReaderWaitsForCompleteJSONLine() throws {
    var reader = ModelFailureEventReader(fileURL: eventsFile)
    reader.skipExistingEvents()
    try append(event(id: "partial", stage: "reducer"), newline: false)

    XCTAssertTrue(try reader.readNewEvents().isEmpty)
    try appendRaw(Data("\n".utf8))

    XCTAssertEqual(try reader.readNewEvents().map(\.id), ["partial"])
  }

  func testNotificationCopyIdentifiesStageModelAndError() {
    let failure = event(id: "copy", stage: "compact")

    XCTAssertEqual(failure.notificationTitle, "OpenChronicle: Compact model failed")
    XCTAssertTrue(failure.notificationBody.contains("gpt-test"))
    XCTAssertTrue(failure.notificationBody.contains("ProviderError"))
    XCTAssertTrue(failure.notificationBody.contains("upstream unavailable"))
  }

  private func event(id: String, stage: String) -> ModelFailureEvent {
    ModelFailureEvent(
      schemaVersion: 1,
      id: id,
      timestamp: "2026-07-18T20:00:00+08:00",
      stage: stage,
      model: "gpt-test",
      errorType: "ProviderError",
      message: "upstream unavailable"
    )
  }

  private func append(_ event: ModelFailureEvent, newline: Bool) throws {
    var data = try JSONEncoder().encode(event)
    if newline { data.append(0x0A) }
    try appendRaw(data)
  }

  private func appendRaw(_ data: Data) throws {
    if !FileManager.default.fileExists(atPath: eventsFile.path) {
      FileManager.default.createFile(atPath: eventsFile.path, contents: nil)
    }
    let handle = try FileHandle(forWritingTo: eventsFile)
    defer { try? handle.close() }
    try handle.seekToEnd()
    try handle.write(contentsOf: data)
  }
}
