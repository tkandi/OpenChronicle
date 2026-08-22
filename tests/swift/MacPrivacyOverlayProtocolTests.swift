import Foundation

private struct Acknowledgement: Decodable {
    let generation: Int
    let rendered: Bool
    let error: String?
}

enum MacPrivacyOverlayProtocolTests {
    static func run() throws {
        guard CommandLine.arguments.count == 2 else {
            throw NSError(domain: "MacPrivacyOverlayProtocolTests", code: 1)
        }
        let helper = CommandLine.arguments[1]

        let unresolved = try runHelper(
            helper,
            input: #"{"generation":12,"state":"protected","style":"pill","displays":[],"all_displays":false}"# + "\n"
        )
        precondition(unresolved.status == 0)
        let unresolvedAck = try JSONDecoder().decode(Acknowledgement.self, from: unresolved.output)
        precondition(unresolvedAck.generation == 12)
        precondition(unresolvedAck.rendered == false)
        precondition(unresolvedAck.error == "unresolved-window-id")

        let exactMarker = "exact-private-marker-must-not-escape"
        let reasonCommand = #"{"generation":13,"state":"protected","style":"pill","displays":[],"all_displays":false,"reason_display":"hybrid","reason_detail":"exact","reason_trigger":"hover","reasons":[{"code":"window_title_rule","window_title":"\#(exactMarker)"}]}"# + "\n"
        let reasonResult = try runHelper(helper, input: reasonCommand)
        precondition(reasonResult.status == 0)
        precondition(!String(data: reasonResult.output, encoding: .utf8)!.contains(exactMarker))
        let reasonAck = try JSONDecoder().decode(Acknowledgement.self, from: reasonResult.output)
        precondition(reasonAck.generation == 13)
        precondition(reasonAck.rendered == false)
        precondition(reasonAck.error == "unresolved-window-id")

        let resumeMarker = "2026-08-22T18:30:00+08:00"
        try expectUnresolved(
            helper,
            generation: 14,
            reasonDetail: "exact",
            reason: ["code": "timed_pause", "effective_resume_at": resumeMarker]
        )
        try expectUnresolved(
            helper,
            generation: 15,
            reasonDetail: "exact",
            reason: ["code": "timed_pause"]
        )
        try expectUnresolved(
            helper,
            generation: 16,
            reasonDetail: "exact",
            reason: [
                "code": "timed_pause",
                "effective_resume_at": String(repeating: "x", count: 170) + "\ncontrol-suffix",
            ]
        )
        try expectUnresolved(
            helper,
            generation: 17,
            reasonDetail: "category",
            reason: ["code": "timed_pause", "effective_resume_at": resumeMarker]
        )

        let invalidResumeType = try runReasonCommand(
            helper,
            generation: 18,
            reasonDetail: "exact",
            reason: ["code": "timed_pause", "effective_resume_at": 123]
        )
        let invalidResumeAck = try JSONDecoder().decode(
            Acknowledgement.self,
            from: invalidResumeType.output
        )
        precondition(invalidResumeAck.generation == -1)
        precondition(invalidResumeAck.rendered == false)
        precondition(invalidResumeAck.error == "invalid-command")

        let privateMarker = "private-marker-must-not-escape"
        let invalid = try runHelper(helper, input: "{\(privateMarker)}\n")
        precondition(invalid.status == 0)
        precondition(!String(data: invalid.output, encoding: .utf8)!.contains(privateMarker))
        let invalidAck = try JSONDecoder().decode(Acknowledgement.self, from: invalid.output)
        precondition(invalidAck.generation == -1)
        precondition(invalidAck.rendered == false)
        precondition(invalidAck.error == "invalid-command")

        print("MacPrivacyOverlayProtocolTests passed")
    }

    private static func expectUnresolved(
        _ helper: String,
        generation: Int,
        reasonDetail: String,
        reason: [String: Any]
    ) throws {
        let result = try runReasonCommand(
            helper,
            generation: generation,
            reasonDetail: reasonDetail,
            reason: reason
        )
        precondition(result.status == 0)
        let outputText = String(decoding: result.output, as: UTF8.self)
        for value in reason.values.compactMap({ $0 as? String }) {
            precondition(!outputText.contains(value))
        }
        let acknowledgement = try JSONDecoder().decode(Acknowledgement.self, from: result.output)
        precondition(acknowledgement.generation == generation)
        precondition(acknowledgement.rendered == false)
        precondition(acknowledgement.error == "unresolved-window-id")
    }

    private static func runReasonCommand(
        _ helper: String,
        generation: Int,
        reasonDetail: String,
        reason: [String: Any]
    ) throws -> (output: Data, status: Int32) {
        let payload: [String: Any] = [
            "generation": generation,
            "state": "paused",
            "style": "pill",
            "displays": [],
            "all_displays": false,
            "reason_display": "hybrid",
            "reason_detail": reasonDetail,
            "reason_trigger": "always",
            "reasons": [reason],
        ]
        let encoded = try JSONSerialization.data(withJSONObject: payload)
        return try runHelper(helper, input: String(decoding: encoded, as: UTF8.self) + "\n")
    }

    private static func runHelper(_ helper: String, input: String) throws -> (output: Data, status: Int32) {
        let process = Process()
        let standardInput = Pipe()
        let standardOutput = Pipe()
        let terminated = DispatchSemaphore(value: 0)

        process.executableURL = URL(fileURLWithPath: helper)
        process.standardInput = standardInput
        process.standardOutput = standardOutput
        process.standardError = Pipe()
        process.terminationHandler = { _ in terminated.signal() }
        try process.run()

        standardInput.fileHandleForWriting.write(Data(input.utf8))
        standardInput.fileHandleForWriting.closeFile()
        guard terminated.wait(timeout: .now() + 5) == .success else {
            process.terminate()
            throw NSError(domain: "MacPrivacyOverlayProtocolTests", code: 2)
        }
        return (standardOutput.fileHandleForReading.readDataToEndOfFile(), process.terminationStatus)
    }
}

try MacPrivacyOverlayProtocolTests.run()
