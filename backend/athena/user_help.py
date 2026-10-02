"""Durable clarification/assistance; a waiting user never holds a model stream open."""
import json
import re

from .errors import UserError
from .store import Store, now, uid


def _record(row):
    item = dict(row)
    item["questions"] = json.loads(item["questions"])
    item["answers"] = json.loads(item["answers"]) if item["answers"] else None
    item["attachments"] = json.loads(item["attachments"])
    return item


def _owner(db, session_id, user_id):
    row = db.execute("SELECT user_id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        raise UserError("not_found", "This conversation was not found.")


def reply_content(record, answers):
    return next(iter(answers.values())) if len(answers) == 1 else "\n\n".join(
        question["question"] + "\n" + answers[question["id"]] for question in record["questions"])


def reply_receipt(record, answers):
    # Freeze the relationship on the message itself. Forked history keeps this
    # receipt without copying/activating a pending user-help interaction.
    return json.dumps({"id": record["id"], "reason": record["reason"], "items": [
        {"id": question["id"], "question": question["question"], "answer": answers[question["id"]]}
        for question in record["questions"]
    ]}, ensure_ascii=False)


def backfill_help_replies(db):
    """Annotate only exact legacy matches; never rewrite protocol history."""
    for row in db.execute("SELECT * FROM user_help WHERE status='answered' AND answers IS NOT NULL ORDER BY resolved_at").fetchall():
        record = _record(row)
        if db.execute("SELECT 1 FROM messages WHERE run_id=? AND help_reply IS NOT NULL AND json_extract(help_reply,'$.id')=?", (record['run_id'], record['id'])).fetchone():
            continue
        # A subsequent request in the same run bounds this answer's identity.
        next_question = db.execute("SELECT MIN(created_at) FROM user_help WHERE run_id=? AND created_at>?", (record['run_id'], record['resolved_at'])).fetchone()[0]
        candidates = db.execute("SELECT id,attachments FROM messages WHERE run_id=? AND role='user' AND help_reply IS NULL AND content=? AND created_at>=? AND (? IS NULL OR created_at<?)",
            (record['run_id'], reply_content(record, record['answers']), record['resolved_at'], next_question, next_question)).fetchall()
        if len(candidates) == 1 and json.loads(candidates[0]['attachments']) == record['attachments']:
            db.execute("UPDATE messages SET help_reply=? WHERE id=?", (reply_receipt(record, record['answers']), candidates[0]['id']))


def validate_questions(questions):
    if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
        raise ValueError("Ask one to three questions at a time.")
    identifiers = set()
    for question in questions:
        if not isinstance(question, dict) or set(question) - {"id", "question", "options"}:
            raise ValueError("Each question needs id, question and optional options.")
        identifier, text = question.get("id"), question.get("question")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier) or identifier in identifiers:
            raise ValueError("Question IDs must be unique, short identifiers.")
        if not isinstance(text, str) or not text.strip() or len(text) > 1500:
            raise ValueError("Question text must contain 1–1500 characters.")
        identifiers.add(identifier)
        options = question.get("options", [])
        if not isinstance(options, list) or len(options) > 6:
            raise ValueError("Offer at most six choices; free text is always available.")
        labels = set()
        for option in options:
            if not isinstance(option, dict) or set(option) - {"label", "description"}:
                raise ValueError("Each choice needs a label and optional description.")
            label, description = option.get("label"), option.get("description", "")
            if not isinstance(label, str) or not label.strip() or len(label) > 200 or label in labels:
                raise ValueError("Choice labels must be nonempty and unique.")
            if not isinstance(description, str) or len(description) > 500:
                raise ValueError("Choice descriptions must be shorter than 500 characters.")
            labels.add(label)


async def request_help(store, session_id, run_id, args):
    questions, reason = args.get("questions"), args.get("reason", "")
    validate_questions(questions)
    if not isinstance(reason, str) or len(reason) > 1500:
        raise ValueError("Help reason must be shorter than 1500 characters.")
    def create(db):
        run = db.execute("SELECT status FROM runs WHERE id=? AND session_id=?", (run_id, session_id)).fetchone()
        if run is None or run["status"] != "running":
            raise ValueError("Request user help from an active run.")
        if db.execute("SELECT 1 FROM user_help WHERE session_id=? AND status IN ('pending','preparing')", (session_id,)).fetchone():
            raise ValueError("This conversation already has an unanswered request.")
        identifier = uid()
        db.execute("INSERT INTO user_help(id,session_id,run_id,status,questions,reason,created_at) VALUES (?,?,?,'preparing',?,?,?)", (identifier, session_id, run_id, json.dumps(questions, ensure_ascii=False), reason, now()))
        return _record(db.execute("SELECT * FROM user_help WHERE id=?", (identifier,)).fetchone())
    request = await store.call(create)
    return {"status": "waiting_for_user", "request": request, "control": {"wait_for_user": request["id"]}}


async def activate_help(store, session_id, run_id, help_id, remaining_tools):
    """Close this assistant's tool slots and suspend in one durable transaction."""
    def activate(db):
        request = db.execute("SELECT * FROM user_help WHERE id=? AND session_id=? AND run_id=? AND status='preparing'", (help_id, session_id, run_id)).fetchone()
        run = db.execute("SELECT status FROM runs WHERE id=? AND session_id=?", (run_id, session_id)).fetchone()
        if request is None or run is None or run["status"] != "running":
            raise ValueError("The help request is no longer active.")
        for tool in remaining_tools:
            Store._message(db, session_id, run_id, "tool", json.dumps({"status": "not_executed", "reason": "Waiting for the user's response. Re-evaluate whether this action is needed after their reply."}), "complete", tool_call_id=tool["id"])
        db.execute("UPDATE user_help SET status='pending' WHERE id=?", (help_id,))
        db.execute("UPDATE runs SET status='waiting_user',phase='waiting_user',finished_at=NULL WHERE id=?", (run_id,))
        return _record(db.execute("SELECT * FROM user_help WHERE id=?", (help_id,)).fetchone())
    return await store.call(activate)


async def list_help(store, session_id, user_id):
    def listing(db):
        _owner(db, session_id, user_id)
        return [_record(row) for row in db.execute("SELECT * FROM user_help WHERE session_id=? AND status='pending' ORDER BY created_at", (session_id,))]
    return await store.call(listing)


async def answer_help(store, session_id, user_id, help_id, answers, attachments=None):
    attachments = attachments or []
    def answer(db):
        _owner(db, session_id, user_id)
        row = db.execute("SELECT * FROM user_help WHERE id=? AND session_id=?", (help_id, session_id)).fetchone()
        if row is None:
            raise UserError("not_found", "This help request was not found.")
        record = _record(row)
        expected = {question["id"] for question in record["questions"]}
        file_reply = len(expected) == 1 and bool(attachments)
        if not isinstance(answers, dict) or set(answers) != expected or any(not isinstance(value, str) or (not value.strip() and not file_reply) or len(value) > 12000 for value in answers.values()):
            raise UserError("invalid_answer", "Answer each question with 1–12000 characters.")
        if record["status"] == "answered":
            if record["answers"] != answers or [item["id"] for item in record["attachments"]] != [item["id"] for item in attachments]:
                raise UserError("help_resolved", "This request already has a different answer.")
            return record, False
        run = db.execute("SELECT status FROM runs WHERE id=?", (record["run_id"],)).fetchone()
        if record["status"] != "pending" or run["status"] != "waiting_user":
            raise UserError("help_resolved", "This request is no longer waiting for an answer.")
        stamp = now()
        db.execute("UPDATE user_help SET status='answered',answers=?,attachments=?,resolved_at=? WHERE id=?", (json.dumps(answers, ensure_ascii=False), json.dumps(attachments, ensure_ascii=False), stamp, help_id))
        # Preserve the original tool result and append the reply: no prefix rewrite and
        # no replay of earlier actions. The source and matching questions remain auditable.
        content = reply_content(record, answers)
        message_id = Store._message(db, session_id, record["run_id"], "user", content, "complete")
        db.execute("UPDATE messages SET help_reply=? WHERE id=?", (reply_receipt(record, answers), message_id))
        if attachments:
            db.execute("UPDATE messages SET attachments=? WHERE id=?", (json.dumps(attachments, ensure_ascii=False), message_id))
        db.execute("UPDATE runs SET status='queued',phase='idle',finished_at=NULL WHERE id=?", (record["run_id"],))
        return _record(db.execute("SELECT * FROM user_help WHERE id=?", (help_id,)).fetchone()), True
    return await store.call(answer)


def cancel_help(db, session_id, run_id=None):
    where, values = "session_id=? AND status IN ('preparing','pending')", [session_id]
    if run_id is not None:
        where += " AND run_id=?"
        values.append(run_id)
    db.execute(f"UPDATE user_help SET status='cancelled',resolved_at=? WHERE {where}", [now(), *values])
