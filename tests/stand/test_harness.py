"""The stand harness against a fake Telegram: guards, interception, the expected
answerCallbackQuery error and the scenarios themselves. No network: the live stand runs only
through `python -m tools.stand`."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, SendMessage

from tools.stand import __main__ as cli
from tools.stand.config import StandConfig, StandConfigError, load_config
from tools.stand.harness import Stand
from tools.stand.recorder import EXPECTED_ANSWER_ERROR, Recorder, StandGuardError

TOKEN = "123456:SECRET-stand-token"
CHAT = -1009990001
ADMIN = 7001
MEMBER = 7002
BOT_ID = 123456
QUERY_TOO_OLD = "Bad Request: query is too old and response timeout expired or query ID is invalid"


class FakeTelegram(BaseSession):
    """Answers like the Bot API, as raw JSON parsed by aiogram's own check_response."""

    def __init__(self, username: str = "stand_test_bot") -> None:
        super().__init__()
        self.username = username
        self.statuses = {ADMIN: "creator", MEMBER: "member", BOT_ID: "member"}
        self.menus: dict[str, list] = {}
        self.fail: dict[str, str] = {}  # method -> description of a 400
        self.methods: list[str] = []
        self.next_message_id = 100

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = method.__api_method__
        self.methods.append(name)
        if name in self.fail:
            reply = {"ok": False, "error_code": 400, "description": self.fail[name]}
            return self.check_response(bot, method, 400, json.dumps(reply)).result
        result = self.result(name, method)
        return self.check_response(
            bot, method, 200, json.dumps({"ok": True, "result": result})
        ).result

    def result(self, name, method):
        me = {"id": BOT_ID, "is_bot": True, "first_name": "Stand", "username": self.username}
        chat = {"id": CHAT, "type": "supergroup", "title": "Stand group"}
        if name == "getMe":
            return me
        if name == "getChatMember":
            user = {"id": method.user_id, "is_bot": False, "first_name": "Real Name"}
            if method.user_id == BOT_ID:
                user = me
            status = self.statuses.get(method.user_id, "left")
            extra = {"is_anonymous": False} if status == "creator" else {}
            return {"status": status, "user": user, **extra}
        if name in ("sendMessage", "editMessageText"):
            self.next_message_id += 1
            message = {
                "message_id": getattr(method, "message_id", None) or self.next_message_id,
                "date": 1_800_000_000,
                "chat": chat,
                "from": me,
                "text": method.text,
            }
            if method.reply_markup is not None:
                message["reply_markup"] = method.reply_markup.model_dump(
                    mode="json", exclude_none=True
                )
            return message
        if name == "answerCallbackQuery":
            raise AssertionError("answered in make_request through self.fail")
        if name == "setMyCommands":
            self.menus[method.scope.type] = [c.model_dump() for c in method.commands]
            return True
        if name == "getMyCommands":
            return self.menus.get(method.scope.type, [])
        raise AssertionError(f"unexpected {name}")


def config(tmp_path: Path) -> StandConfig:
    return StandConfig(TOKEN, CHAT, ADMIN, MEMBER, runs_dir=tmp_path / "runs")


def fake_bot(session: FakeTelegram) -> Bot:
    return Bot(TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@pytest.fixture
def telegram() -> FakeTelegram:
    session = FakeTelegram()
    session.fail["answerCallbackQuery"] = QUERY_TOO_OLD  # Telegram never sent the query
    return session


async def run(tmp_path, telegram, scenario="smoke"):
    code, path = await cli.run_scenario(config(tmp_path), scenario, bot=fake_bot(telegram))
    return code, json.loads(path.read_text()), path


# --- config ------------------------------------------------------------------


def test_no_config_names_every_missing_variable(tmp_path):
    with pytest.raises(StandConfigError) as exc:
        load_config({}, tmp_path / ".env.stand")
    for name in ("TOKEN", "CHAT_ID", "ADMIN_ID", "MEMBER_ID"):
        assert f"PIGEON_STAND_{name}" in str(exc.value)
    assert ".env.stand.example" in str(exc.value)


def test_partial_config_names_only_what_is_missing(tmp_path):
    env_file = tmp_path / ".env.stand"
    env_file.write_text(f"PIGEON_STAND_TOKEN={TOKEN}\nPIGEON_STAND_CHAT_ID={CHAT}\n")
    with pytest.raises(StandConfigError) as exc:
        load_config({"PIGEON_STAND_ADMIN_ID": " "}, env_file)
    assert "PIGEON_STAND_ADMIN_ID" in str(exc.value)
    assert "PIGEON_STAND_MEMBER_ID" in str(exc.value)
    assert "PIGEON_STAND_CHAT_ID" not in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_non_integer_id_is_rejected(tmp_path):
    env = {
        "PIGEON_STAND_TOKEN": TOKEN,
        "PIGEON_STAND_CHAT_ID": "@group",
        "PIGEON_STAND_ADMIN_ID": "1",
        "PIGEON_STAND_MEMBER_ID": "2",
    }
    with pytest.raises(StandConfigError, match="PIGEON_STAND_CHAT_ID must be an integer"):
        load_config(env, None)


def test_environment_overrides_file_and_quotes_are_stripped(tmp_path):
    env_file = tmp_path / ".env.stand"
    env_file.write_text(
        "# local stand\n"
        f'export PIGEON_STAND_TOKEN="{TOKEN}"\n'
        "PIGEON_STAND_CHAT_ID=-1\nPIGEON_STAND_ADMIN_ID=1\nPIGEON_STAND_MEMBER_ID='2'\n"
    )
    loaded = load_config({"PIGEON_STAND_CHAT_ID": str(CHAT), "PIGEON_BOT_TOKEN": "x"}, env_file)
    assert (loaded.token, loaded.chat_id, loaded.admin_id, loaded.member_id) == (
        TOKEN,
        CHAT,
        1,
        2,
    )
    assert TOKEN not in repr(loaded)


def test_cli_without_config_exits_2_before_any_network(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in list(__import__("os").environ):
        if key.startswith("PIGEON_STAND_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(cli, "new_bot", lambda _: pytest.fail("no bot without a config"))
    assert cli.main(["smoke"]) == 2
    assert "The test stand is not configured" in capsys.readouterr().err


# --- guards --------------------------------------------------------------------


@pytest.mark.parametrize("username", ["golubinskity_bot", "Golubinskity_Bot"])
async def test_production_bot_is_refused_after_get_me_alone(tmp_path, username):
    telegram = FakeTelegram(username=username)
    code, report, path = await run(tmp_path, telegram)
    assert code == 2
    assert telegram.methods == ["getMe"]
    assert "golubinskity_bot" in report["error"].lower()
    assert not (path.parent / "stand.db").exists()


async def test_bot_outside_the_stand_chat_is_refused(tmp_path, telegram):
    telegram.statuses[BOT_ID] = "left"
    code, report, _ = await run(tmp_path, telegram)
    assert code == 2
    assert telegram.methods == ["getMe", "getChatMember"]
    assert "not in the stand chat" in report["error"]


async def test_private_chat_id_is_refused(tmp_path, telegram):
    stand_config = StandConfig(TOKEN, 42, ADMIN, MEMBER, runs_dir=tmp_path / "runs")
    code, _ = await cli.run_scenario(stand_config, "smoke", bot=fake_bot(telegram))
    assert code == 2
    assert telegram.methods == ["getMe"]


async def test_recorder_blocks_calls_before_get_me_and_outside_the_stand_chat(telegram):
    bot = fake_bot(telegram)
    recorder = Recorder(CHAT)
    bot.session.middleware(recorder)
    with pytest.raises(StandGuardError, match="before getMe"):
        await bot(SendMessage(chat_id=CHAT, text="early"))
    recorder.verified = True
    with pytest.raises(StandGuardError, match="not the stand chat"):
        await bot(SendMessage(chat_id=CHAT + 1, text="elsewhere"))
    with pytest.raises(StandGuardError, match="not the stand chat"):
        await bot(SendMessage(chat_id="@public_channel", text="elsewhere"))
    assert telegram.methods == []  # nothing reached Telegram
    await bot(SendMessage(chat_id=CHAT, text="ok"))
    assert telegram.methods == ["sendMessage"]
    assert recorder.calls[-1]["params"]["parse_mode"] == "HTML"
    assert recorder.calls[-1]["result"]["text"] == "ok"


async def test_answer_error_is_masked_only_for_a_synthetic_query(telegram):
    bot = fake_bot(telegram)
    recorder = Recorder(CHAT)
    recorder.verified = True
    recorder.synthetic_queries.add("stand-1")
    bot.session.middleware(recorder)
    assert await bot(AnswerCallbackQuery(callback_query_id="stand-1", text="hi")) is True
    assert recorder.calls[-1]["expected_error"] == EXPECTED_ANSWER_ERROR
    assert recorder.calls[-1]["ok"] is False
    with pytest.raises(TelegramBadRequest):
        await bot(AnswerCallbackQuery(callback_query_id="real-query", text="hi"))
    telegram.fail["answerCallbackQuery"] = "Bad Request: MESSAGE_TOO_LONG"
    with pytest.raises(TelegramBadRequest):
        await bot(AnswerCallbackQuery(callback_query_id="stand-1", text="hi"))
    assert "expected_error" not in recorder.calls[-1]


# --- scenarios -----------------------------------------------------------------


async def test_smoke_passes_and_records_every_call(tmp_path, telegram, monkeypatch):
    monkeypatch.setenv("PIGEON_DB_PATH", str(tmp_path / "prod.db"))
    code, report, path = await run(tmp_path, telegram)
    assert code == 0, report["summary"]
    assert report["passed"]
    assert not (tmp_path / "prod.db").exists()
    assert report["database"] == str(path.parent / "stand.db")

    steps = {s["step"]: s for s in report["steps"]}
    tap = steps["spit by the member"]
    assert (
        tap["update"]["callback_query"]["message"]["message_id"]
        == (steps["arrive by the admin"]["calls"][-1]["result"]["message_id"])
    )
    assert [c["method"] for c in tap["calls"]] == ["answerCallbackQuery", "editMessageText"]
    assert tap["calls"][0]["expected_error"] == EXPECTED_ANSWER_ERROR
    anonymous = steps["quiet hours by an anonymous admin"]
    assert anonymous["update"]["message"]["sender_chat"]["id"] == CHAT
    assert "getChatMember" not in [c["method"] for c in anonymous["calls"]]
    refused = steps["arrive refused to the member"]
    assert [c["method"] for c in refused["calls"]] == ["getChatMember", "sendMessage"]
    assert all(c["ok"] for c in report["global_checks"])


async def test_reports_never_contain_the_token_and_public_one_hides_stand_ids(tmp_path, telegram):
    _, _, path = await run(tmp_path, telegram)
    full = path.read_text()
    public = (path.parent / "report.public.json").read_text()
    assert TOKEN not in full and TOKEN not in public
    assert str(CHAT) in full
    for private in (
        str(CHAT),
        str(ADMIN),
        str(MEMBER),
        str(BOT_ID),
        "stand_test_bot",
        "Real Name",
        str(tmp_path),
    ):
        assert private not in public, private
    assert "<stand_chat_id>" in public
    titles = [s["step"] for s in json.loads(public)["steps"]]
    assert "spit by the member" in titles  # step names survive redaction


async def test_a_member_with_admin_rights_fails_the_permission_check(tmp_path, telegram):
    telegram.statuses[MEMBER] = "creator"  # any admin status must fail the member cell
    code, report, _ = await run(tmp_path, telegram)
    assert code == 1
    assert "arrive refused to the member" in report["summary"]["failed_steps"]


async def test_another_answer_error_fails_the_run(tmp_path, telegram):
    telegram.fail["answerCallbackQuery"] = "Bad Request: MESSAGE_TOO_LONG"
    code, report, _ = await run(tmp_path, telegram)
    assert code == 1
    assert "spit by the member" in report["summary"]["failed_steps"]
    assert not report["global_checks"][0]["ok"]


async def test_markup_rejected_by_telegram_fails_the_run(tmp_path, telegram):
    telegram.fail["sendMessage"] = "Bad Request: can't parse entities"
    code, report, _ = await run(tmp_path, telegram)
    assert code == 1
    assert report["steps"][2]["error"].startswith("TelegramBadRequest")


async def test_each_run_gets_its_own_database(tmp_path, telegram):
    _, first, _ = await run(tmp_path, telegram)
    second_telegram = FakeTelegram()
    second_telegram.fail["answerCallbackQuery"] = QUERY_TOO_OLD
    stand = Stand(config(tmp_path), fake_bot(second_telegram), tmp_path / "other", "smoke")
    assert str(stand.db_path) != first["database"]


HAS_MENUS = importlib.util.find_spec("pigeon.telegram.commands") is not None


@pytest.mark.skipif(not HAS_MENUS, reason="this revision has no command menus (ADR-0013)")
async def test_commands_scenario_passes_where_menus_exist(tmp_path, telegram):
    code, report, _ = await run(tmp_path, telegram, "commands")
    assert code == 0, report["summary"]


@pytest.mark.skipif(HAS_MENUS, reason="this revision has the command menus (ADR-0013)")
async def test_commands_scenario_fails_clearly_without_menus(tmp_path, telegram):
    code, report, _ = await run(tmp_path, telegram, "commands")
    assert code == 1
    assert "ADR-0013" in report["steps"][-1]["error"]
    assert "sendMessage" not in telegram.methods
