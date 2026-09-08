from terminalgames.engine.journal import Journal, JournalEntry


def make_entry(entry_id="lead1", category="lead"):
    return JournalEntry(id=entry_id, category=category, text="A lead.", discovered_at="c1:s1")


def test_add_and_has():
    journal = Journal()
    assert not journal.has("lead1")
    journal.add(make_entry())
    assert journal.has("lead1")


def test_add_is_idempotent():
    journal = Journal()
    journal.add(make_entry())
    journal.add(make_entry())
    assert journal.count() == 1


def test_count_and_by_category():
    journal = Journal()
    journal.add(make_entry("lead1", "lead"))
    journal.add(make_entry("trace1", "trace"))
    journal.add(make_entry("trace2", "trace"))
    assert journal.count() == 3
    assert journal.count("trace") == 2
    assert {e.id for e in journal.by_category("trace")} == {"trace1", "trace2"}


def test_roundtrip_dict():
    journal = Journal()
    journal.add(make_entry())
    restored = Journal.from_dict(journal.to_dict())
    assert restored.has("lead1")
    assert restored.count() == 1


def test_from_dict_handles_none():
    assert Journal.from_dict(None).count() == 0
