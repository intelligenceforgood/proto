from __future__ import annotations

from types import SimpleNamespace

from i4g.scripts import saved_searches


def test_annotate_records_appends_tag_and_schema_version():
    records = [
        {
            "name": "Test",
            "tags": ["legacy"],
            "params": {"query": "wallet"},
        }
    ]

    annotated = saved_searches.annotate_records(records, tag="hybrid-v1", schema_version="v2", dedupe=True)

    assert annotated[0]["tags"] == ["legacy", "hybrid-v1"]
    assert annotated[0]["params"]["schema_version"] == "v2"


def test_annotate_records_handles_missing_fields():
    annotated = saved_searches.annotate_records(
        [{}],
        tag=" hybrid-v1 ",
        schema_version="",
        dedupe=True,
    )

    assert annotated[0]["tags"] == ["hybrid-v1"]
    assert "params" not in annotated[0]


def test_argument_parser_defaults_follow_settings(monkeypatch):
    stub_settings = SimpleNamespace(
        search=SimpleNamespace(saved_search=SimpleNamespace(migration_tag="taggy-v2", schema_version="schema-v3"))
    )
    monkeypatch.setattr(saved_searches, "SETTINGS", stub_settings)

    parser = saved_searches.build_argument_parser()
    args = parser.parse_args(["--input", "input.json"])

    assert args.tag == "taggy-v2"
    assert args.schema_version == "schema-v3"
