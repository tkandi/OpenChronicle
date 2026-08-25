import Foundation
import XCTest

@testable import OpenChronicleApp

final class ConfigurationTests: XCTestCase {
  private func snapshotPayload(path: String) -> String {
    #"""
    {
      "schema_version": 1,
      "path": "\#(path)",
      "sha256": "abc123",
      "valid": true,
      "error": null,
      "contains_direct_api_keys": false,
      "values": {
        "models": {
          "default": {
            "model": "gpt-default", "base_url": "", "api_key_env": "OPENAI_API_KEY",
            "max_tokens": null, "model_explicit": true, "uses_direct_api_key": false
          },
          "timeline": {
            "model": "gpt-timeline", "base_url": "", "api_key_env": "OPENAI_API_KEY",
            "max_tokens": null, "model_explicit": true, "uses_direct_api_key": false
          },
          "reducer": {
            "model": "gpt-default", "base_url": "", "api_key_env": "OPENAI_API_KEY",
            "max_tokens": null, "model_explicit": false, "uses_direct_api_key": false
          },
          "classifier": {
            "model": "gpt-default", "base_url": "", "api_key_env": "OPENAI_API_KEY",
            "max_tokens": null, "model_explicit": false, "uses_direct_api_key": false
          },
          "compact": {
            "model": "gpt-default", "base_url": "", "api_key_env": "OPENAI_API_KEY",
            "max_tokens": null, "model_explicit": false, "uses_direct_api_key": false
          }
        },
        "capture": {
          "event_driven": true, "heartbeat_minutes": 10,
          "buffer_retention_hours": 168, "screenshot_retention_hours": 24,
          "buffer_max_mb": 2000, "include_screenshot": true,
          "screenshot_monitor": "separate", "screenshot_privacy_mode": "skip-monitor",
          "screenshot_privacy_fail_closed": true, "screenshot_jpeg_quality": 80,
          "privacy_indicator_style": "pill",
          "privacy_indicator_placement": "bottom-left-flush",
          "privacy_reason_display": "hybrid", "privacy_reason_detail": "exact",
          "privacy_reason_trigger": "hover",
          "privacy_counts": {
            "deny_app_names": 1, "deny_bundle_ids": 0,
            "deny_window_title_patterns": 1, "deny_url_patterns": 0,
            "deny_text_patterns": 0
          }
        },
        "timeline": {"window_minutes": 1},
        "session": {"gap_minutes": 5, "flush_minutes": 5},
        "reducer": {"enabled": true},
        "classifier": {"interval_minutes": 30},
        "memory": {"auto_dormant_days": 30},
        "search": {"default_top_k": 5},
        "mcp": {
          "auto_start": true, "transport": "streamable-http",
          "host": "127.0.0.1", "port": 8742
        }
      }
    }
    """#
  }

  private func privacySnapshotPayload(path: String) -> String {
    #"""
    {
      "schema_version": 1,
      "path": "\#(path)",
      "sha256": "abc123",
      "valid": true,
      "error": null,
      "values": {
        "deny_app_names": ["Mail"],
        "deny_bundle_ids": [],
        "deny_window_title_patterns": ["Private"],
        "deny_url_patterns": [],
        "deny_text_patterns": []
      }
    }
    """#
  }

  func testConfigurationDraftEmitsOnlyChangesAndRemovesOverride() throws {
    let payload = snapshotPayload(path: "/tmp/config.toml")
    let snapshot = try JSONDecoder().decode(
      ConfigurationSnapshot.self,
      from: Data(payload.utf8)
    )
    let original = try XCTUnwrap(ConfigurationDraft(snapshot: snapshot))
    XCTAssertEqual(original.privacyIndicatorStyle, "pill")
    XCTAssertEqual(original.privacyIndicatorPlacement, "bottom-left-flush")
    var edited = original
    edited.heartbeatMinutes = 15
    edited.timelineModelOverride = nil
    edited.privacyIndicatorStyle = "border"
    edited.privacyIndicatorPlacement = "bottom-left-inset"

    let updates = edited.updates(comparedTo: original)

    XCTAssertEqual(updates.count, 4)
    XCTAssertEqual(updates["capture.heartbeat_minutes"] as? Int, 15)
    XCTAssertTrue(updates["models.timeline.model"] is NSNull)
    XCTAssertEqual(updates["capture.privacy_indicator_style"] as? String, "border")
    XCTAssertEqual(
      updates["capture.privacy_indicator_placement"] as? String,
      "bottom-left-inset"
    )
  }

  func testPrivacyReasonSettingsEmitOnlyChangedPaths() throws {
    let snapshot = try JSONDecoder().decode(
      ConfigurationSnapshot.self,
      from: Data(snapshotPayload(path: "/tmp/config.toml").utf8)
    )
    let original = try XCTUnwrap(ConfigurationDraft(snapshot: snapshot))
    var edited = original
    edited.privacyReasonDisplay = "diagnostics"
    edited.privacyReasonDetail = "category"
    edited.privacyReasonTrigger = "click"

    let updates = edited.updates(comparedTo: original)

    XCTAssertEqual(updates.count, 3)
    XCTAssertEqual(updates["capture.privacy_reason_display"] as? String, "diagnostics")
    XCTAssertEqual(updates["capture.privacy_reason_detail"] as? String, "category")
    XCTAssertEqual(updates["capture.privacy_reason_trigger"] as? String, "click")
  }

  func testScreenshotPrivacyWindowModesAreAcceptedFromSnapshots() throws {
    let payload = snapshotPayload(path: "/tmp/config.toml")

    for mode in ["mask-window", "exclude-window"] {
      let variant = payload.replacingOccurrences(
        of: "\"screenshot_privacy_mode\": \"skip-monitor\"",
        with: "\"screenshot_privacy_mode\": \"\(mode)\""
      )
      let snapshot = try JSONDecoder().decode(
        ConfigurationSnapshot.self,
        from: Data(variant.utf8)
      )

      XCTAssertEqual(
        try XCTUnwrap(ConfigurationDraft(snapshot: snapshot)).screenshotPrivacyMode,
        mode
      )
    }
  }

  func testScreenshotPrivacyModePickerOffersAllSupportedModes() {
    XCTAssertEqual(
      Set(ScreenshotPrivacyModeOption.allCases.map(\.rawValue)),
      Set(["off", "skip-monitor", "mask-window", "exclude-window"])
    )
  }

  func testPrivacyIndicatorStyleDefaultsForMissingAndUnknownSnapshotValues() throws {
    let payload = snapshotPayload(path: "/tmp/config.toml")
    let missing = payload.replacingOccurrences(
      of: "          \"privacy_indicator_style\": \"pill\",\n",
      with: ""
    )
    let unknown = payload.replacingOccurrences(
      of: "\"privacy_indicator_style\": \"pill\"",
      with: "\"privacy_indicator_style\": \"future-style\""
    )

    for variant in [missing, unknown] {
      let snapshot = try JSONDecoder().decode(
        ConfigurationSnapshot.self,
        from: Data(variant.utf8)
      )
      XCTAssertEqual(
        try XCTUnwrap(ConfigurationDraft(snapshot: snapshot)).privacyIndicatorStyle,
        "pill"
      )
    }
  }

  func testPrivacyIndicatorPlacementDefaultsForMissingAndUnknownSnapshotValues() throws {
    let payload = snapshotPayload(path: "/tmp/config.toml")
    let missing = payload.replacingOccurrences(
      of: "          \"privacy_indicator_placement\": \"bottom-left-flush\",\n",
      with: ""
    )
    let unknown = payload.replacingOccurrences(
      of: "\"privacy_indicator_placement\": \"bottom-left-flush\"",
      with: "\"privacy_indicator_placement\": \"future-placement\""
    )

    for variant in [missing, unknown] {
      let snapshot = try JSONDecoder().decode(
        ConfigurationSnapshot.self,
        from: Data(variant.utf8)
      )
      XCTAssertEqual(
        try XCTUnwrap(ConfigurationDraft(snapshot: snapshot)).privacyIndicatorPlacement,
        PrivacyIndicatorPlacementOption.defaultValue.rawValue
      )
    }
  }

  func testPrivacyReasonSettingsDefaultForMissingAndUnknownSnapshotValues() throws {
    let payload = snapshotPayload(path: "/tmp/config.toml")
    let missing = payload
      .replacingOccurrences(of: "          \"privacy_reason_display\": \"hybrid\", \"privacy_reason_detail\": \"exact\",\n", with: "")
      .replacingOccurrences(of: "          \"privacy_reason_trigger\": \"hover\",\n", with: "")
    let unknown = payload
      .replacingOccurrences(of: "\"privacy_reason_display\": \"hybrid\"", with: "\"privacy_reason_display\": \"future-display\"")
      .replacingOccurrences(of: "\"privacy_reason_detail\": \"exact\"", with: "\"privacy_reason_detail\": \"future-detail\"")
      .replacingOccurrences(of: "\"privacy_reason_trigger\": \"hover\"", with: "\"privacy_reason_trigger\": \"future-trigger\"")

    for variant in [missing, unknown] {
      let snapshot = try JSONDecoder().decode(
        ConfigurationSnapshot.self,
        from: Data(variant.utf8)
      )
      let draft = try XCTUnwrap(ConfigurationDraft(snapshot: snapshot))
      XCTAssertEqual(draft.privacyReasonDisplay, "hybrid")
      XCTAssertEqual(draft.privacyReasonDetail, "exact")
      XCTAssertEqual(draft.privacyReasonTrigger, "hover")
    }
  }

  func testPrivacyReasonPickerAvailabilityUsesDisplayAndIndicatorStyle() {
    for display in PrivacyReasonDisplayOption.allCases {
      let state = PrivacyReasonPickerAvailability(
        display: display,
        indicatorStyle: .pill
      )
      XCTAssertTrue(state.isDisplayEnabled)
      XCTAssertTrue(state.isDetailEnabled)
      XCTAssertEqual(state.isTriggerEnabled, display != .diagnostics)
    }

    for display in PrivacyReasonDisplayOption.allCases {
      let state = PrivacyReasonPickerAvailability(
        display: display,
        indicatorStyle: .off
      )
      XCTAssertTrue(state.isDisplayEnabled)
      XCTAssertTrue(state.isDetailEnabled)
      XCTAssertFalse(state.isTriggerEnabled)
    }
  }

  func testReasonPresentationKeepsEightRowsAndUsesStableTruncation() {
    let exactValue = String(repeating: "x", count: 160)
    let fixtureCodes: [ProtectionReasonDiagnosticCode] = [
      .windowTitleRule,
      .appRule,
      .bundleRule,
      .windowTitleUnknown,
      .modeAllInherited,
      .manualPause,
      .inventoryUnavailable,
      .indicatorUnconfirmed,
    ]
    let reasons = fixtureCodes.enumerated().map { index, code in
      ProtectionReasonDiagnostic(
        code: code,
        displayID: 1,
        windowTitle: index == 0 ? exactValue : "Window \(index)"
      )
    }

    let descriptors = reasons.map {
      ProtectionReasonPresentationDescriptor(
        reason: $0,
        detail: .exact,
        showsExactValues: true
      )
    }

    XCTAssertEqual(descriptors.count, 8)
    XCTAssertTrue(descriptors.allSatisfy { $0.detailLineLimit == 2 })
    XCTAssertTrue(descriptors.allSatisfy { $0.truncation == .middle })
    XCTAssertTrue(descriptors[0].detail?.contains(exactValue) == true)
  }

  func testPresentationStateInvalidUsesFixedFailurePresentation() {
    let descriptor = ProtectionReasonPresentationDescriptor(
      reason: ProtectionReasonDiagnostic(
        code: .presentationStateInvalid,
        displayID: nil
      ),
      detail: .category,
      showsExactValues: false
    )

    XCTAssertEqual(descriptor.title, "Protection state invalid")
    XCTAssertEqual(descriptor.systemImage, "exclamationmark.triangle.fill")
  }

  func testReasonPresentationUsesFixedPlaceholderWhileExactValuesAreHidden() {
    let marker = String(repeating: "private", count: 20)
    let descriptor = ProtectionReasonPresentationDescriptor(
      reason: ProtectionReasonDiagnostic(
        code: .windowTitleRule,
        displayID: 1,
        windowTitle: marker,
        rule: marker
      ),
      detail: .tiered,
      showsExactValues: false
    )

    XCTAssertEqual(
      descriptor.detail,
      ProtectionReasonPresentationDescriptor.hiddenExactValuePlaceholder
    )
    XCTAssertFalse(descriptor.detail?.contains(marker) == true)
  }

  func testZeroDisplayGlobalFailuresProduceVisibleReasonSections() {
    let presentation = ProtectionDiagnosticsDetailPresentation.make(
      selectedDisplayID: nil,
      displays: [],
      globalReasons: [
        ProtectionReasonDiagnostic(code: .inventoryUnavailable, displayID: nil),
        ProtectionReasonDiagnostic(code: .pauseStateUnavailable, displayID: nil),
      ],
      detail: .category,
      showsExactValues: false
    )

    XCTAssertNil(presentation.emptyMessage)
    XCTAssertEqual(presentation.sections.count, 1)
    XCTAssertEqual(presentation.sections[0].title, "Global reasons")
    XCTAssertEqual(
      presentation.sections[0].descriptors.map(\.title),
      ["Display inventory unavailable", "Pause state unavailable"]
    )
  }

  func testZeroDisplayGlobalExactValuesRemainConcealedInPresentation() {
    let marker = "private-global-diagnostic-marker"
    let presentation = ProtectionDiagnosticsDetailPresentation.make(
      selectedDisplayID: nil,
      displays: [],
      globalReasons: [
        ProtectionReasonDiagnostic(
          code: .helperParse,
          displayID: nil,
          windowTitle: marker,
          rule: marker
        )
      ],
      detail: .exact,
      showsExactValues: false
    )

    let descriptor = presentation.sections[0].descriptors[0]
    XCTAssertEqual(
      descriptor.detail,
      ProtectionReasonPresentationDescriptor.hiddenExactValuePlaceholder
    )
    XCTAssertFalse(descriptor.title.contains(marker))
    XCTAssertFalse(descriptor.detail?.contains(marker) == true)
  }

  func testPrivacyDraftEmitsArrayChangesAndRejectsBlankRules() throws {
    let payload = privacySnapshotPayload(path: "/tmp/config.toml")
    let snapshot = try JSONDecoder().decode(
      PrivacyConfigurationSnapshot.self,
      from: Data(payload.utf8)
    )
    let original = try XCTUnwrap(PrivacyConfigurationDraft(snapshot: snapshot))
    var edited = original
    edited.denyAppNames.append("Passwords")

    let updates = edited.updates(comparedTo: original)

    XCTAssertEqual(updates.count, 1)
    XCTAssertEqual(
      updates["capture.deny_app_names"] as? [String],
      ["Mail", "Passwords"]
    )
    XCTAssertNil(edited.validationError)
    edited.denyURLPatterns.append("   ")
    XCTAssertEqual(edited.validationError, "URL patterns cannot contain an empty rule.")
  }

  @MainActor
  func testConfigurationControllerLoadsAndSendsPatch() async throws {
    let root = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }

    let configFile = root.appendingPathComponent("config.toml")
    try Data("[capture]\nheartbeat_minutes = 10\n".utf8).write(to: configFile)
    let requestFile = root.appendingPathComponent("request.json")
    let executable = root.appendingPathComponent("fake-openchronicle")
    let payload = snapshotPayload(path: configFile.path)
    let privacyPayload = privacySnapshotPayload(path: configFile.path)
    let script = """
      #!/bin/sh
      case "$*" in
        "config --json")
          printf '%s\\n' '\(payload)'
          ;;
        "config --privacy-json")
          printf '%s\\n' '\(privacyPayload)'
          ;;
        "config --patch-json")
          input="$(cat)"
          printf '%s' "$input" > '\(requestFile.path)'
          printf '%s\\n' '{"ok":true,"changed":true,"path":"\(configFile.path)","backup":"\(configFile.path).backup","sha256":"next"}'
          ;;
        *)
          printf '%s\\n' '{"ok":false,"error":"unexpected arguments"}'
          exit 9
          ;;
      esac
      """
    try Data(script.utf8).write(to: executable)
    try FileManager.default.setAttributes(
      [.posixPermissions: 0o755],
      ofItemAtPath: executable.path
    )

    let paths = RuntimePaths(root: root)
    let locator = BackendLocator(
      environment: ["OPENCHRONICLE_BIN": executable.path],
      homeDirectory: root,
      bundleResources: nil
    )
    let controller = ConfigurationController(paths: paths, locator: locator)

    await controller.load()
    XCTAssertNil(controller.lastError)
    XCTAssertEqual(controller.draft?.heartbeatMinutes, 10)
    XCTAssertEqual(
      controller.snapshot?.values?.capture.privacyCount("deny_app_names"),
      1
    )
    await controller.loadPrivacy()
    XCTAssertNil(controller.lastError)
    XCTAssertEqual(controller.privacyDraft?.denyAppNames, ["Mail"])
    controller.updateDraft(\.heartbeatMinutes, value: 15)
    controller.updatePrivacyDraft(\.denyAppNames, value: ["Mail", "Passwords"])
    XCTAssertTrue(controller.hasCommonChanges)
    XCTAssertTrue(controller.hasPrivacyChanges)
    XCTAssertTrue(controller.hasFormChanges)

    let changed = await controller.saveCommon()

    XCTAssertTrue(changed)
    let requestData = try Data(contentsOf: requestFile)
    let request = try XCTUnwrap(
      JSONSerialization.jsonObject(with: requestData) as? [String: Any]
    )
    XCTAssertEqual(request["expected_sha256"] as? String, "abc123")
    let updates = try XCTUnwrap(request["updates"] as? [String: Any])
    XCTAssertEqual(updates["capture.heartbeat_minutes"] as? Int, 15)
    XCTAssertEqual(
      updates["capture.deny_app_names"] as? [String],
      ["Mail", "Passwords"]
    )
  }

  @MainActor
  func testRuntimePolicyPromotesSavedSnapshotOnlyAfterBackendPIDChanges() async throws {
    let root = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }

    let configFile = root.appendingPathComponent("config.toml")
    try Data("[capture]\nprivacy_reason_display = \"hybrid\"\n".utf8).write(to: configFile)
    let savedMarker = root.appendingPathComponent("saved")
    let executable = root.appendingPathComponent("fake-openchronicle")
    let initialPayload = snapshotPayload(path: configFile.path)
    let savedPayload = initialPayload
      .replacingOccurrences(of: "\"sha256\": \"abc123\"", with: "\"sha256\": \"next123\"")
      .replacingOccurrences(
        of: "\"privacy_reason_display\": \"hybrid\"",
        with: "\"privacy_reason_display\": \"overlay\""
      )
      .replacingOccurrences(
        of: "\"privacy_reason_detail\": \"exact\"",
        with: "\"privacy_reason_detail\": \"category\""
      )
    let script = """
      #!/bin/sh
      case "$*" in
        "config --json")
          if [ -f '\(savedMarker.path)' ]; then
            printf '%s\\n' '\(savedPayload)'
          else
            printf '%s\\n' '\(initialPayload)'
          fi
          ;;
        "config --patch-json")
          cat >/dev/null
          touch '\(savedMarker.path)'
          printf '%s\\n' '{"ok":true,"changed":true,"path":"\(configFile.path)","backup":null,"sha256":"next123"}'
          ;;
        *)
          printf '%s\\n' '{"ok":false,"error":"unexpected arguments"}'
          exit 9
          ;;
      esac
      """
    try Data(script.utf8).write(to: executable)
    try FileManager.default.setAttributes(
      [.posixPermissions: 0o755],
      ofItemAtPath: executable.path
    )
    let controller = ConfigurationController(
      paths: RuntimePaths(root: root),
      locator: BackendLocator(
        environment: ["OPENCHRONICLE_BIN": executable.path],
        homeDirectory: root,
        bundleResources: nil
      )
    )

    await controller.load()
    XCTAssertEqual(controller.activeSnapshot, controller.snapshot)
    XCTAssertTrue(controller.observeBackendPID(111))
    XCTAssertEqual(
      PrivacyReasonRuntimePolicy(snapshot: controller.activeSnapshot),
      PrivacyReasonRuntimePolicy(display: .hybrid, detail: .exact)
    )

    controller.updateDraft(\.privacyReasonDisplay, value: "overlay")
    controller.updateDraft(\.privacyReasonDetail, value: "category")
    let saved = await controller.saveCommon()
    XCTAssertTrue(saved)
    XCTAssertEqual(controller.snapshot?.sha256, "next123")
    XCTAssertEqual(
      PrivacyReasonRuntimePolicy(snapshot: controller.activeSnapshot),
      PrivacyReasonRuntimePolicy(display: .hybrid, detail: .exact)
    )
    XCTAssertFalse(controller.observeBackendPID(111))
    XCTAssertEqual(
      PrivacyReasonRuntimePolicy(snapshot: controller.activeSnapshot),
      PrivacyReasonRuntimePolicy(display: .hybrid, detail: .exact)
    )

    XCTAssertFalse(controller.observeBackendPID(nil))
    XCTAssertTrue(controller.observeBackendPID(222))
    XCTAssertEqual(controller.activeSnapshot, controller.snapshot)
    XCTAssertEqual(
      PrivacyReasonRuntimePolicy(snapshot: controller.activeSnapshot),
      PrivacyReasonRuntimePolicy(display: .overlay, detail: .category)
    )
  }
}
