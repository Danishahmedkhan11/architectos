"""Blueprint-alignment gates: secret redaction, prompt policy, provider chains, citation audit."""
import textwrap

from backend import prompts
from backend.ingest import ingest_repo, redact_secrets


def test_redact_secrets_patterns():
    text = textwrap.dedent('''
        aws = "AKIAIOSFODNN7EXAMPLE"
        openai_key = "sk-abcdefghijklmnopqrstuvwxyz123456"
        api_key = "super-secret-value-123456"
        harmless = os.getenv("JWT_SECRET", "short")
    ''')
    redacted, count = redact_secrets(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert "super-secret-value-123456" not in redacted
    assert count >= 3
    assert 'os.getenv("JWT_SECRET", "short")' in redacted  # no false positive


def test_ingest_redacts_and_skips_key_files(tmp_path):
    repo = tmp_path / "leaky"
    repo.mkdir()
    (repo / "config.py").write_text(
        'DB_URL = "postgres://localhost/db"\n'
        'aws_token = "AKIAIOSFODNN7EXAMPLE"\n',
        encoding="utf-8",
    )
    (repo / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nxxx\n", encoding="utf-8")
    (repo / "server.pem").write_text("-----BEGIN PRIVATE KEY-----\nxxx\n", encoding="utf-8")
    g = ingest_repo(repo, name="leaky")
    assert g.stats()["redactions"] >= 1
    assert "id_rsa" not in g.nodes and "server.pem" not in g.nodes
    everything = " ".join(
        (n.get("etext") or "") + (n.get("snippet") or "") for n in g.nodes.values()
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in everything


def test_prompts_declare_untrusted_source_policy():
    for prompt in (
        prompts.ASK_SYSTEM, prompts.IMPACT_SYSTEM,
        prompts.CODEGEN_SYSTEM, prompts.ARCHITECTURE_SYSTEM,
    ):
        assert "UNTRUSTED DATA" in prompt
    for prompt in (prompts.ASK_SYSTEM, prompts.IMPACT_SYSTEM, prompts.ARCHITECTURE_SYSTEM):
        assert "[[node_id]]" in prompt


def test_openrouter_chain_is_vendor_prefixed(monkeypatch):
    from backend import config, llm

    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(llm, "_openrouter_ids", [])  # listing unavailable -> static prefixing
    monkeypatch.setattr(llm, "_resolved", {})
    chain = llm._model_chain("reason")
    assert chain and all("/" in m for m in chain)
    assert chain[0] == "openai/gpt-5.6-sol"
    assert llm._model_chain("codex")[0] == "openai/gpt-5.3-codex"


def test_openrouter_chain_prefers_available_models(monkeypatch):
    from backend import config, llm

    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(llm, "_openrouter_ids", ["openai/gpt-5.5", "openai/gpt-5.7-nova", "anthropic/claude"])
    monkeypatch.setattr(llm, "_resolved", {})
    chain = llm._model_chain("reason")
    assert chain[0] == "openai/gpt-5.5"          # best available from the preference list
    assert "openai/gpt-5.7-nova" in chain         # unknown newer model still reachable
    assert "anthropic/claude" not in chain        # other vendors not auto-included
