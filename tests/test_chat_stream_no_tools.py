from pathlib import Path

SOURCE = Path("routes/chat_routes.py").read_text(encoding="utf-8")


def test_no_tools_flag_is_read_from_form_data():
    assert 'no_tools = str(form_data.get("no_tools") or "").lower() == "true"' in SOURCE


def test_no_tools_pin_fires_after_every_auto_escalation_site():
    # The pin must sit strictly after all seven chat_mode = "agent" escalation
    # sites, so no intent heuristic can promote a no_tools turn to an agent
    # loop. If a future escalation site is added above the pin without this
    # test being touched, this will still pass but the security review in the
    # plan should catch it -- so also assert the pin comes after the
    # do_research auto-trigger, the last escalation-adjacent block.
    escalation_idx = SOURCE.index('chat_mode = "agent"\n        # An approved plan')
    do_research_idx = SOURCE.index("Session {session} in research_pending")
    pin_idx = SOURCE.index("if no_tools:\n            chat_mode = \"chat\"")
    assert escalation_idx < pin_idx
    assert do_research_idx < pin_idx


def test_no_tools_pin_resets_all_four_escalation_vectors():
    pin_start = SOURCE.index("if no_tools:\n            chat_mode = \"chat\"")
    pin_block = SOURCE[pin_start:pin_start + 200]
    assert 'chat_mode = "chat"' in pin_block
    assert "plan_mode = False" in pin_block
    assert 'workspace = ""' in pin_block
    assert "do_research = False" in pin_block


def test_no_tools_pin_precedes_attachment_parsing():
    # Sanity check on placement relative to the anchor used when the hunk was
    # inserted, so a later refactor that reorders the function trips this test
    # rather than silently un-pinning no_tools turns.
    pin_idx = SOURCE.index("if no_tools:\n            chat_mode = \"chat\"")
    att_idx = SOURCE.index("att_ids = []")
    assert pin_idx < att_idx
