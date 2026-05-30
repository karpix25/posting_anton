from app.services.generation_prompt_rules import build_cta_case_instruction, extract_cta_tokens


def test_extract_cta_tokens_preserves_case_and_order():
    assert extract_cta_tokens("CTA first", "folder/cta/video", "CTA repeated") == ["CTA", "cta"]


def test_build_cta_case_instruction_lists_exact_tokens():
    instruction = build_cta_case_instruction("Use cTa in copy")

    assert "cTa" in instruction
    assert "Не меняй CTA на cta" in instruction


def test_build_cta_case_instruction_omits_rule_without_cta():
    assert build_cta_case_instruction("regular prompt", "folder/video.mp4") == ""
