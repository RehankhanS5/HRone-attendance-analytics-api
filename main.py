"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import json
from bson.json_util import dumps as bson_dumps
from fastapi.responses import Response
from bson.json_util import dumps
from bson.json_util import dumps as bson_dumps, loads as bson_loads
from pickle import dumps
from typing import Optional, Literal
from pydantic import BaseModel, Field, StrictInt
from pymongo import ReturnDocument
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from decimal import Decimal, ROUND_HALF_UP
from pydantic import BaseModel
from pymongo import MongoClient



load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]


def create_indexes():
    # Employees: unique employee code
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employee_emp_code"
    )

    # Attendance: one record per employee per date
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_date"
    )

    # Attendance list filtering and date sorting
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="ix_attendance_date_emp"
    )

    # Monthly analytics by employee and date
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1), ("status", 1)],
        name="ix_attendance_emp_date_status"
    )

    print("MongoDB indexes created successfully")


IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
@app.on_event("startup")
def startup_event():
    create_indexes()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    # MongoDB stores naive datetimes as UTC.
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)

    # Convert the punch-in instant to Indian Standard Time.
    punch_in_ist = punch_in.astimezone(IST).replace(microsecond=0)

    hour, minute = map(int, shift_start.split(":"))

    shift_start_ist = punch_in_ist.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    elapsed_seconds = (
        punch_in_ist - shift_start_ist
    ).total_seconds()

    # The grace period is 10 minutes.
    if elapsed_seconds <= 600:
        return 0

    # Return whole minutes since shift start, not after the grace period.
    return int(elapsed_seconds // 60)



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)

    seconds = Decimal(str(
        (punch_out - punch_in).total_seconds()
    ))
    hours = (seconds / Decimal("3600")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return float(hours)


def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str,
    shift_start: str = "09:30",
) -> int:
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)

    punch_out_ist = punch_out.astimezone(IST).replace(microsecond=0)

    end_hour, end_minute = map(int, shift_end.split(":"))
    start_hour, start_minute = map(int, shift_start.split(":"))

    shift_end_ist = datetime.fromisoformat(date_str).replace(
        hour=end_hour,
        minute=end_minute,
        tzinfo=IST,
    )

    if (end_hour, end_minute) <= (start_hour, start_minute):
        shift_end_ist += timedelta(days=1)

    overtime_seconds = (
        punch_out_ist - shift_end_ist
    ).total_seconds()

    whole_minutes = int(overtime_seconds // 60)
    return whole_minutes if whole_minutes >= 30 else 0


def round_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = (seconds / Decimal("3600")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return float(hours)


def calculate_overtime(
    punch_out: datetime,
    attendance_date: str,
    shift_start: str,
    shift_end: str,
) -> int:
    shift_end_hour, shift_end_minute = map(int, shift_end.split(":"))
    shift_start_hour, shift_start_minute = map(int, shift_start.split(":"))

    shift_end_dt = datetime.fromisoformat(attendance_date).replace(
        hour=shift_end_hour,
        minute=shift_end_minute,
        tzinfo=IST,
    )

    if (shift_end_hour, shift_end_minute) <= (
        shift_start_hour,
        shift_start_minute,
    ):
        shift_end_dt += timedelta(days=1)

    # Stored MongoDB datetimes are naive UTC.
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)

    overtime = int(
        (
            punch_out.astimezone(IST) - shift_end_dt
        ).total_seconds() // 60
    )

    return overtime if overtime >= 30 else 0


def to_epoch_ms(value):
    if value is None:
        return None

    # MongoDB stores naive datetimes as UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return int(value.timestamp() * 1000)




# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeRequest(BaseModel):
    status: Optional[
        Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
    ] = None

    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None

    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}

    if department:
        q["department"] = department

    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        ts_utc = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        ts_utc = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).replace(microsecond=0)

    # Store naive UTC in MongoDB; calculate the calendar date in IST.
    ts_db = ts_utc.replace(tzinfo=None)
    d = ts_utc.astimezone(IST).date().isoformat()

    if db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "date": d}
    ):
        raise HTTPException(409, "already punched in for this date")

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts_db,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts_utc, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    db.attendance_logs.insert_one(doc)

    # Return timestamps as epoch milliseconds.
    doc["punch_in"] = int(ts_utc.timestamp() * 1000)
    return doc


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        punch_out_utc = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        punch_out_utc = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).replace(microsecond=0)

    punch_out_db = punch_out_utc.replace(tzinfo=None)

    # Find the latest punch-in that occurred no later than punch-out.
    record = db.attendance_logs.find_one(
    {
        "emp_code": body.emp_code,
        "punch_in": {"$ne": None},
        "punch_out": None,
    },
    sort=[("punch_in", -1)],
)

    if not record:
        raise HTTPException(status_code=404, detail="No punch-in found")

    punch_in_db = record["punch_in"]

    # Compare instants as UTC. MongoDB stores naive UTC datetimes.
    if punch_in_db.tzinfo is None:
        punch_in_utc = punch_in_db.replace(tzinfo=timezone.utc)
    else:
        punch_in_utc = punch_in_db.astimezone(timezone.utc)

    elapsed_seconds = (punch_out_utc - punch_in_utc).total_seconds()

    if elapsed_seconds <= 0 or elapsed_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and no more than 24 hours later",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="This attendance record is already punched out",
        )

    work_hours = round_work_hours(punch_in_db, punch_out_db)
    overtime_minutes = calculate_overtime(
        punch_out_utc,
        record["date"],
        emp["shift_start"],
        emp["shift_end"],
    )
    half_day = work_hours < 4.50

    # Conditional update ensures only one concurrent request can close the record.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_out_db,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="This attendance record is already punched out",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated.pop("_id", None)

    # Convert stored UTC instants to epoch milliseconds for the API.
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    updated["history"] = updated.get("history", [])
    updated["late_minutes"] = updated.get("late_minutes", 0)
    updated["overtime_minutes"] = updated.get("overtime_minutes", 0)
    updated["half_day"] = updated.get("half_day", False)

    return updated


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}

        if date_from:
            q["date"]["$gte"] = date_from

        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    # Count matching records in MongoDB.
    total = db.attendance_logs.count_documents(q)

    # Sort and paginate in MongoDB instead of loading all records.
    page_docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    # Convert timestamps to epoch milliseconds.
    for record in page_docs:
        for field in ("punch_in", "punch_out"):
            record[field] = to_epoch_ms(record.get(field))

        # Convert timestamps in attendance history too.
        for entry in record.get("history", []):
            entry["at"] = to_epoch_ms(entry.get("at"))

            for field in ("punch_in", "punch_out"):
                if field in entry.get("changes", {}):
                    entry["changes"][field] = to_epoch_ms(
                        entry["changes"][field]
                    )

    return {
        "items": page_docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    # Validate the requested attendance date.
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d").date()
        if parsed_date.isoformat() != date:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")

    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0},
    )
    if not employee:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if not record:
        raise HTTPException(404, "Attendance record not found")

    old_status = record["status"]
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    new_status = body.status if body.status is not None else old_status

    # ABSENT and LEAVE records cannot have punch timestamps.
    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                422,
                "ABSENT or LEAVE cannot include punch timestamps",
            )
        new_in = None
        new_out = None
    else:
        def to_utc(value):
            if value is None:
                return None
            return datetime.fromtimestamp(
                value // 1000, tz=timezone.utc
            ).replace(tzinfo=None)

        new_in = to_utc(body.punch_in) if body.punch_in is not None else old_in
        new_out = to_utc(body.punch_out) if body.punch_out is not None else old_out

        if new_in is None:
            raise HTTPException(
                422,
                "Presence status requires punch_in",
            )

        # The punch-in instant must fall on this IST attendance date.
        punch_in_utc = new_in.replace(tzinfo=timezone.utc)
        if punch_in_utc.astimezone(IST).date().isoformat() != date:
            raise HTTPException(
                422,
                "punch_in must fall on the attendance date in IST",
            )

        if new_out is not None:
            elapsed = (new_out - new_in).total_seconds()
            if elapsed <= 0 or elapsed > 24 * 60 * 60:
                raise HTTPException(
                    422,
                    "punch_out must be after punch_in and within 24 hours",
                )

    # Recalculate all derived fields from the final timestamps.
    if new_in is not None and new_out is not None:
        work_hours = round_work_hours(new_in, new_out)
        overtime_minutes = calculate_overtime(
            new_out.replace(tzinfo=timezone.utc),
            date,
            employee["shift_start"],
            employee["shift_end"],
        )
        half_day = work_hours < 4.50
    else:
        work_hours = None
        overtime_minutes = 0
        half_day = False

    if new_in is not None:
        late_minutes = compute_late_minutes(
            new_in.replace(tzinfo=timezone.utc),
            employee["shift_start"],
        )
    else:
        late_minutes = 0

    proposed = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    changes = {}
    for field, new_value in proposed.items():
        old_value = record.get(field)
        if old_value != new_value:
            changes[field] = new_value

    if not changes:
        raise HTTPException(422, "No attendance fields changed")

    history_entry = {
        "at": datetime.now(timezone.utc).replace(tzinfo=None),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Compare-and-set prevents concurrent edits from overwriting history.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": old_status,
            "punch_in": old_in,
            "punch_out": old_out,
            "history": record.get("history", []),
        },
        {
            "$set": proposed,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            409,
            "Attendance changed concurrently; fetch the record and retry",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    # Return all instants as epoch milliseconds.
    def epoch_ms(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp()) * 1000

    for field in ("punch_in", "punch_out"):
        updated[field] = epoch_ms(updated.get(field))

    for entry in updated.get("history", []):
        entry["at"] = epoch_ms(entry["at"])
        for field in ("punch_in", "punch_out"):
            if field in entry.get("changes", {}):
                entry["changes"][field] = epoch_ms(
                    entry["changes"][field]
                )

    return updated


from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def round_half_up(value, places=2):
    if value is None:
        return None
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    )


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate YYYY-MM.
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM")

    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0},
    )
    if not employee:
        raise HTTPException(404, "Employee not found")

    year = month_start.year
    month_number = month_start.month

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    joined_on = datetime.strptime(
        employee["joined_on"], "%Y-%m-%d"
    ).date()

    # Count Monday-Friday working days from joining date.
    first_day = max(month_start, joined_on)
    working_days = 0
    current = first_day

    while current <= month_end:
        if current.weekday() < 5:
            working_days += 1
        current += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {
                                        "$in": [
                                            {"$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$date"
                                                }
                                            }},
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    "$half_day",
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    stats = result[0] if result else {}

    present_days = stats.get("present_days", 0)
    attendance_pct = (
        round_half_up(present_days / working_days * 100, 2)
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": round_half_up(present_days, 2),
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: str | None = None
):
    import re
    from datetime import date, timedelta

    # Validate month format
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    try:
        year, month_num = map(int, month.split("-"))
        month_start = date(year, month_num, 1)

        if month_num == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_num + 1, 1)

        month_end = next_month - timedelta(days=1)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid month"
        )

    # Employees who joined on or before the end of the month
    employee_match = {
        "joined_on": {"$lte": month_end.isoformat()}
    }

    if department:
        employee_match["department"] = department

    # Department headcount, including employees with no attendance logs
    employees = list(db.employees.find(
        employee_match,
        {"_id": 0, "emp_code": 1, "department": 1}
    ))

    if not employees:
        return {"month": month, "items": []}

    emp_codes = [employee["emp_code"] for employee in employees]

    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": emp_codes},
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat()
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": [
                                        {"$dayOfWeek": {
                                            "$dateFromString": {
                                                "dateString": "$date"
                                            }
                                        }},
                                        [2, 3, 4, 5, 6]
                                    ]}
                                ]
                            },
                            {"$cond": ["$half_day", 0.5, 1]},
                            0
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": ["$work_hours", None]}
                                ]
                            },
                            "$work_hours",
                            0
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": ["$work_hours", None]}
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}
                }
            }
        }
    ]

    attendance_results = list(db.attendance_logs.aggregate(pipeline))
    by_employee = {
        row["_id"]: row for row in attendance_results
    }

    # Combine employee results into department totals
    department_totals = {}

    for employee in employees:
        dept = employee["department"]
        row = by_employee.get(employee["emp_code"], {})

        totals = department_totals.setdefault(dept, {
            "department": dept,
            "headcount": 0,
            "present_days": 0.0,
            "work_hours_sum": 0.0,
            "work_hours_count": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "leave_count": 0,
            "on_duty_count": 0
        })

        totals["headcount"] += 1
        totals["present_days"] += row.get("present_days", 0)
        totals["work_hours_sum"] += row.get("work_hours_sum", 0)
        totals["work_hours_count"] += row.get("work_hours_count", 0)
        totals["late_count"] += row.get("late_count", 0)
        totals["total_late_minutes"] += row.get("total_late_minutes", 0)
        totals["leave_count"] += row.get("leave_count", 0)
        totals["on_duty_count"] += row.get("on_duty_count", 0)

    items = []

    for dept in sorted(department_totals):
        totals = department_totals[dept]
        count = totals.pop("work_hours_count")
        hours_sum = totals.pop("work_hours_sum")

        avg_hours = (
            round_half_up(hours_sum / count, 2)
            if count else None
        )

        totals["present_days"] = round_half_up(
            totals["present_days"], 2
        )
        totals["avg_work_hours"] = avg_hours
        items.append(totals)

    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = Query(default=10, ge=1, le=50),
    department: str | None = None
):
    import re
    from datetime import date, timedelta

    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    try:
        year, month_num = map(int, month.split("-"))
        month_start = date(year, month_num, 1)

        if month_num == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_num + 1, 1)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    employee_match = {}
    if department:
        employee_match["department"] = department

    employees = list(db.employees.find(
        employee_match,
        {"_id": 0, "emp_code": 1, "name": 1, "department": 1}
    ))

    if not employees:
        return {"month": month, "items": []}

    employee_by_code = {
        employee["emp_code"]: employee for employee in employees
    }

    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": list(employee_by_code.keys())},
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat()
                },
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        }
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    ranked = []
    previous_minutes = None
    current_rank = 0

    for index, result in enumerate(results, start=1):
        minutes = result["total_late_minutes"]

        if minutes != previous_minutes:
            current_rank = index
            previous_minutes = minutes

        employee = employee_by_code[result["_id"]]

        ranked.append({
            "rank": current_rank,
            "emp_code": result["_id"],
            "name": employee["name"],
            "department": employee["department"],
            "total_late_minutes": minutes,
            "late_count": result["late_count"]
        })

    # Apply limit after ranking so ties at the cutoff are included.
    ranked = [item for item in ranked if item["rank"] <= limit]

    return {"month": month, "items": ranked}

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(alias="from"),
    to_date: str = Query(alias="to")
):
    import re
    from datetime import date, timedelta

    # Validate date format
    for value in (from_date, to_date):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise HTTPException(
                status_code=422,
                detail="Dates must use YYYY-MM-DD format"
            )

    try:
        start_date = date.fromisoformat(from_date)
        end_date = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date")

    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'"
        )

    if (end_date - start_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days"
        )

    # A department is valid if it has at least one employee.
    employees = list(db.employees.find(
        {"department": department},
        {"_id": 0, "emp_code": 1, "joined_on": 1}
    ))

    if not employees:
        raise HTTPException(
            status_code=404,
            detail="Department not found"
        )

    employee_codes = [e["emp_code"] for e in employees]

    # Attendance records grouped by day in MongoDB.
    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": employee_codes},
                "date": {
                    "$gte": start_date.isoformat(),
                    "$lte": end_date.isoformat()
                }
            }
        },
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {

                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            {"$cond": ["$half_day", 0.5, 1]},
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateFromString": {
                        "dateString": "$_id"
                    }
                },
                "present_count": 1,
                "late_count": 1
            }
        },
        {
    "$densify": {
        "field": "date",
        "range": {
            "step": 1,
            "unit": "day",
            "bounds": [
                datetime.combine(start_date, datetime.min.time()),
                datetime.combine(
                    end_date + timedelta(days=1),
                    datetime.min.time()
                )
            ]
        }
    }
},

        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                        "timezone": "UTC"
                    }
                },
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]}
            }
        },
        {"$sort": {"date": 1}}
    ]

    try:
        daily_records = list(db.attendance_logs.aggregate(pipeline))
        print("DEBUG: aggregation succeeded:", len(daily_records))
    except Exception as exc:
        print("DEBUG: aggregation failed:", repr(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    # Build a joined-on lookup for calculating daily headcount.
    joined_dates = {}
    for employee in employees:
        joined = employee.get("joined_on")
        if isinstance(joined, str):
            joined = date.fromisoformat(joined)
        elif isinstance(joined, date):
            pass
        else:
            continue

        joined_dates[employee["emp_code"]] = joined

    # Headcount and rate for each day.
    # Note: the final contract-compliant version should calculate
    # these fields and the moving average entirely in MongoDB.
    
    # Calculate daily headcount, attendance rate, and 7-day moving average.
    items = []
    previous_rates = []

    for record in daily_records:
        current_day = date.fromisoformat(record["date"])
        is_working_day = current_day.weekday() < 5

        headcount = sum(
            1 for joined in joined_dates.values()
            if joined <= current_day
        )

        present_count = record.get("present_count", 0)
        late_count = record.get("late_count", 0)

        attendance_rate = None
        if is_working_day and headcount > 0:
            attendance_rate = round_half_up(
                present_count / headcount, 4
            )

        # Keep one entry per calendar day. Weekend rates are None,
        # but weekend dates still occupy a position in the 7-day window.
        previous_rates.append(attendance_rate)
        window = [
            rate for rate in previous_rates[-7:]
            if rate is not None
        ]

        moving_avg = (
            round_half_up(sum(window) / len(window), 4)
            if window else None
        )

        # Append every date, including weekends.
        items.append({
            "date": record["date"],
            "is_working_day": is_working_day,
            "headcount": headcount,
            "present_count": present_count,
            "late_count": late_count,
            "attendance_rate": attendance_rate,
            "moving_avg_7d": moving_avg
        })


    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def admin_explain(
    endpoint: str,
    emp_code: str | None = None,
    month: str | None = None,
    department: str | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    import re
    from datetime import date

    

    allowed_endpoints = {
        "attendance_list",
        "department_summary",
        "department_trend",
        "employee_monthly",
        "late_leaderboard",
    }

    print("ADMIN EXPLAIN RUNNING FROM:", __file__)
    print("ENDPOINT RECEIVED:", repr(endpoint))

    endpoint = endpoint.strip().lower()

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid endpoint '{endpoint}'. Allowed values: {sorted(allowed_endpoints)}",
        )
        

    def validate_month(value):
        if not value or not re.fullmatch(r"\d{4}-\d{2}", value):
            raise HTTPException(
                status_code=422,
                detail="A valid month in YYYY-MM format is required",
            )

        try:
            year, month_number = map(int, value.split("-"))
            start = date(year, month_number, 1)

            if month_number == 12:
                next_start = date(year + 1, 1, 1)
            else:
                next_start = date(year, month_number + 1, 1)

            return start.isoformat(), next_start.isoformat()

        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month")

    collection_name = "attendance_logs"

    # 1. Attendance list
    if endpoint == "attendance_list":
        filters = {}

        if emp_code:
            filters["emp_code"] = emp_code

        if status:
            filters["status"] = status

        if date_from or date_to:
            filters["date"] = {}

            if date_from:
                filters["date"]["$gte"] = date_from

            if date_to:
                filters["date"]["$lte"] = date_to

        command = {
            "find": "attendance_logs",
            "filter": filters,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
            "projection": {"_id": 0},
        }

    # 2. Employee monthly analytics
    elif endpoint == "employee_monthly":
        if not emp_code:
            raise HTTPException(
                status_code=422,
                detail="emp_code is required for employee_monthly",
            )

        start, next_start = validate_month(month)

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {"$gte": start, "$lt": next_start},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "records": {"$sum": 1},
                }
            },
        ]

        command = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }

    # 3. Department summary or late leaderboard
    elif endpoint in ("department_summary", "late_leaderboard"):
        start, next_start = validate_month(month)

        employee_filter = {}
        if department:
            employee_filter["department"] = department

        employees = list(
            db.employees.find(
                employee_filter,
                {"_id": 0, "emp_code": 1},
            )
        )

        employee_codes = [employee["emp_code"] for employee in employees]

        match = {
            "emp_code": {"$in": employee_codes},
            "date": {"$gte": start, "$lt": next_start},
        }

        if endpoint == "late_leaderboard":
            match["late_minutes"] = {"$gt": 0}

            group = {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }

            sort_stage = {
                "$sort": {"total_late_minutes": -1, "_id": 1}
            }

        else:
            group = {
                "_id": "$emp_code",
                "record_count": {"$sum": 1},
            }

            sort_stage = {"$sort": {"_id": 1}}

        pipeline = [
            {"$match": match},
            {"$group": group},
            sort_stage,
        ]

        command = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }

    # 4. Department trend
    else:
        if not department:
            raise HTTPException(
                status_code=422,
                detail="department is required for department_trend",
            )

        if not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="Both from and to are required for department_trend",
            )

        try:
            start_day = date.fromisoformat(from_date)
            end_day = date.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="from and to must use YYYY-MM-DD format",
            )

        if (
            end_day < start_day
            or (end_day - start_day).days + 1 > 92
        ):
            raise HTTPException(
                status_code=422,
                detail="Invalid date range; maximum range is 92 days",
            )

        employees = list(
            db.employees.find(
                {"department": department},
                {"_id": 0, "emp_code": 1},
            )
        )

        if not employees:
            raise HTTPException(
                status_code=404,
                detail="Department not found",
            )

        employee_codes = [employee["emp_code"] for employee in employees]

        pipeline = [
            {
                "$match": {
                    "emp_code": {"$in": employee_codes},
                    "date": {
                        "$gte": start_day.isoformat(),
                        "$lte": end_day.isoformat(),
                    },
                }
            },
            {
                "$group": {
                    "_id": "$date",
                    "records": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]

        command = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }

    # Execute MongoDB explain with execution statistics.
        
    # Execute MongoDB explain with execution statistics.
    
    from bson.json_util import dumps as bson_dumps, loads as bson_loads

    try:
        explain_result = db.command({
            "explain": command,
            "verbosity": "executionStats",
        })
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"MongoDB explain failed: {type(exc).__name__}: {exc}",
        )

    try:
        serialized = bson_dumps(explain_result)
        explain_json = bson_loads(serialized)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Explain serialization failed: {type(exc).__name__}: {exc}",
        )

    
    
    
    response_json = bson_dumps({
        "endpoint": endpoint,
        "collection": collection_name,
        "explain": explain_result,
    })

    if isinstance(response_json, bytes):
        response_bytes = response_json
    else:
        response_bytes = response_json.encode("utf-8")

    return Response(
        content=response_bytes,
        media_type="application/json",
    )




# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
