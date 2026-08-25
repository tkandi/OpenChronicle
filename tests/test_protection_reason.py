from openchronicle.capture.protection_reason import (
    DisplayProtectionReasons,
    ProtectionReason,
    ProtectionReasonCode,
    ProtectionReasonState,
    sanitize_reason_value,
)


def test_reason_values_are_bounded_and_control_char_free() -> None:
    raw = "private\nwindow\t" + "x" * 300

    cleaned = sanitize_reason_value(raw)

    assert cleaned == "private window " + "x" * 144 + "…"
    assert len(cleaned) == 160


def test_category_payload_never_contains_exact_values() -> None:
    reason = ProtectionReason(
        code=ProtectionReasonCode.WINDOW_TITLE_RULE,
        display_id=2,
        app_name="Private Browser",
        window_title="Secret Account",
        rule="InPrivate",
    )

    payload = reason.to_payload(detail="category")

    assert payload == {"code": "window_title_rule", "display_id": 2}
    assert "Secret" not in repr(payload)


def test_exact_payload_includes_only_bounded_reason_fields() -> None:
    reason = ProtectionReason(
        code=ProtectionReasonCode.WINDOW_TITLE_RULE,
        display_id=2,
        source_display_id=1,
        app_name="Edge\n",
        bundle_id="com.microsoft.edgemac",
        window_title="InPrivate\t",
        rule="InPrivate",
    )

    assert reason.to_payload(detail="exact") == {
        "code": "window_title_rule",
        "display_id": 2,
        "source_display_id": 1,
        "app_name": "Edge ",
        "bundle_id": "com.microsoft.edgemac",
        "window_title": "InPrivate ",
        "rule": "InPrivate",
    }


def test_presentation_state_invalid_is_a_fixed_failure_reason() -> None:
    reason = ProtectionReason(
        ProtectionReasonCode.PRESENTATION_STATE_INVALID,
        display_id=None,
    )

    assert reason.state is ProtectionReasonState.FAILED
    assert reason.priority == 600
    assert reason.to_payload(detail="category") == {
        "code": "presentation_state_invalid",
        "display_id": None,
    }


def test_display_reasons_choose_fixed_priority_primary_and_count_extras() -> None:
    reasons = DisplayProtectionReasons(
        (
            ProtectionReason(ProtectionReasonCode.MODE_ALL_INHERITED, 2),
            ProtectionReason(ProtectionReasonCode.WINDOW_TITLE_UNKNOWN, 2),
            ProtectionReason(ProtectionReasonCode.APP_RULE, 2, app_name="Passwords"),
            ProtectionReason(ProtectionReasonCode.DIAGNOSTICS_REVEAL, 2),
            ProtectionReason(ProtectionReasonCode.TIMED_PAUSE, None),
            ProtectionReason(ProtectionReasonCode.HELPER_EXIT, None),
        )
    )

    display_reasons = reasons.for_display(2)

    assert [reason.code for reason in display_reasons] == [
        ProtectionReasonCode.HELPER_EXIT,
        ProtectionReasonCode.TIMED_PAUSE,
        ProtectionReasonCode.DIAGNOSTICS_REVEAL,
        ProtectionReasonCode.APP_RULE,
        ProtectionReasonCode.WINDOW_TITLE_UNKNOWN,
        ProtectionReasonCode.MODE_ALL_INHERITED,
    ]
    assert reasons.primary_for_display(2) is display_reasons[0]
    assert reasons.additional_count_for_display(2) == 5


def test_display_reasons_include_global_failures_and_bound_each_display_to_eight() -> None:
    global_failure = ProtectionReason(ProtectionReasonCode.HELPER_EXIT, None)
    direct_reasons = tuple(
        ProtectionReason(ProtectionReasonCode.APP_RULE, 2, rule=f"rule-{index}")
        for index in range(10)
    )
    reasons = DisplayProtectionReasons((global_failure, *direct_reasons))

    display_reasons = reasons.for_display(2)

    assert display_reasons[0] == global_failure
    assert len(display_reasons) == 8
    assert reasons.additional_count_for_display(2) == 7
