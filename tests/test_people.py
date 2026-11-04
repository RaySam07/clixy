"""His people: found however they're said or heard, learned from chats Clixy opens, told."""

from __future__ import annotations

import pytest

import people


@pytest.mark.parametrize("a, b, close", [
    ("Vasif", "Wasif", True), ("Wasif", "Waseeeeeeef", True), ("Zayan Manu", "Zayaan Manu", True),
    ("Wasif", "Wajid", False), ("Wasif", "Zayan", False),
])
def test_names_match_however_they_are_heard(a, b, close):
    assert (people.alike(a, b) >= 0.75) is close


def test_a_chat_list_item_is_its_name_only():
    assert people.chat_name("Waseeeeeeef 12:30 PM k") == "Waseeeeeeef"
    assert people.chat_name("Zayaan Manu Yesterday see you there") == "Zayaan Manu"
    assert people.chat_name("Mum") == "Mum"


def test_opening_a_chat_for_a_name_he_said_teaches_its_whatsapp_name():
    book = people.PeopleBook()
    book.learn_chat("go on to WhatsApp web and find Wasif", "Waseeeeeeef 12:30 PM k")
    assert [(p.name, p.whatsapp) for p in book.people] == [("Wasif", "Waseeeeeeef")]
    assert book.learn_chat("open whatsapp and find wasif", "Family group 9:02 AM ok") is None, "not who he said"
    assert [p.name for p in book.mentioned("tell Vasif what the plan is for today")] == ["Wasif"]
    assert book.mentioned("open settings and turn on dark mode") == []


def test_what_he_tells_it_is_kept_and_told_back():
    book = people.PeopleBook()
    book.remember("Wasif", who="flatmate", email="wasif@example.com", style="short and casual")
    book.remember("Vasif", whatsapp="Waseeeeeeef")          # the same person, heard another way
    assert len(book.people) == 1
    text = book.summary("message vasif i'll be late")
    assert text.startswith("the people he means: Wasif (flatmate; whatsapp chat \"Waseeeeeeef\"; wasif@example.com; "
                           "he writes to them: short and casual")


def test_person_tags_in_a_reply_are_kept_and_never_spoken(tmp_path):
    rest = people.keep_told("got it, wasif's your flatmate. [PERSON:Wasif|who:flatmate|email:wasif@example.com]")
    assert rest == "got it, wasif's your flatmate."
    again = people.PeopleBook.load(tmp_path / "memory" / "people.json")
    assert again.people[0].who == "flatmate" and again.people[0].email == "wasif@example.com"


def test_nothing_is_kept_or_told_while_history_is_off(monkeypatch):
    monkeypatch.setattr(people, "_keeping", lambda: False)
    assert people.keep_told("noted. [PERSON:Wasif|who:flatmate]") == "noted."
    people.learn_chat("find wasif", "Waseeeeeeef 12:30 PM k")
    assert people.current().people == [] and people.summary("tell wasif") == ""


def test_forget_history_forgets_his_people_and_laptop_too(tmp_path, monkeypatch):
    import laptop
    import tray
    monkeypatch.setattr(tray, "MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setattr(tray, "INDEX_DB_PATH", str(tmp_path / "index.db"))
    people.keep_told("noted. [PERSON:Wasif|who:flatmate]")
    laptop.current().see("chrome.exe", "(95) WhatsApp - Google Chrome")
    assert (tmp_path / "memory" / "people.json").exists()
    tray.forget_history()
    assert not (tmp_path / "memory" / "people.json").exists()
    assert people.current().people == [] and laptop.current().sites == {}
