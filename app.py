from sqlite3 import OperationalError
from flask import Flask, render_template, request, redirect, session, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "secret123"
app.config["UPLOAD_FOLDER"] = "static/images/rooms"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max file size

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///hotel.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ----------------- Validation Helpers -----------------
def safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_valid_date(s):
    try:
        datetime.strptime(s, '%Y-%m-%d')
        return True
    except Exception:
        return False


def require_fields(source, fields):
    missing = [f for f in fields if not source.get(f)]
    return missing


def allowed_file(filename):
    return filename and filename.rsplit('.', 1)[-1].lower() in ("jpg", "jpeg", "png")


def validate_password(password):
    """Return (True, '') if password meets policy, otherwise (False, message)."""
    if not password:
        return False, "password required"
    if len(password) < 8:
        return False, "minimum 8 characters"
    if len(password) > 50:
        return False, "maximum 50 characters"
    # require lowercase, uppercase, digit, special char
    import re
    if not re.search(r"[a-z]", password):
        return False, "must contain a lowercase letter"
    if not re.search(r"[A-Z]", password):
        return False, "must contain an uppercase letter"
    if not re.search(r"\d", password):
        return False, "must contain a digit"
    if not re.search(r"[!@#$%^&*()_+\-=[\]{};':\"\\|,.<>/?~]", password):
        return False, "must contain a special character"
    return True, ""



# ─── Custom Data Structure: Singly Linked List for Rooms ────────────────────────

class RoomNode:
    def __init__(self, room):
        self.room = room
        self.next = None


class RoomLinkedList:
    def __init__(self):
        self.head = None
        self._size = 0

    def append(self, room):
        node = RoomNode(room)
        if self.head is None:
            self.head = node
        else:
            current = self.head
            while current.next:
                current = current.next
            current.next = node
        self._size += 1

    def __len__(self):
        return self._size

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.room)
            current = current.next
        return result

    def find_by_id(self, room_id):
        current = self.head
        while current:
            if current.room.id == room_id:
                return current.room
            current = current.next
        return None

    def find_by_number(self, room_number):
        current = self.head
        while current:
            if current.room.room_number == room_number:
                return current.room
            current = current.next
        return None

    def get_available_rooms(self):
        avail = RoomLinkedList()
        current = self.head
        while current:
            if current.room.status == "Available":
                avail.append(current.room)
            current = current.next
        return avail


# Global cache
room_cache = RoomLinkedList()


@app.before_request
def refresh_room_cache():
    global room_cache
    room_cache = RoomLinkedList()
    for room in Room.query.order_by(Room.id).all():
        room_cache.append(room)


# ─── Helper Functions ───────────────────────────────────────────────────────────

def calculate_days(check_in, check_out):
    try:
        # allow either string dates or datetime objects
        if isinstance(check_in, str):
            check_in_date = datetime.strptime(check_in, '%Y-%m-%d')
        else:
            check_in_date = check_in

        if isinstance(check_out, str):
            check_out_date = datetime.strptime(check_out, '%Y-%m-%d')
        else:
            check_out_date = check_out

        days = (check_out_date - check_in_date).days
        return max(days, 1)
    except Exception:
        return 1


# ─── Models ─────────────────────────────────────────────────────────────────────

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(50))
    role = db.Column(db.String(20))   # admin / receptionist / user


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_number = db.Column(db.String(20))
    room_type = db.Column(db.String(50))
    price = db.Column(db.Integer)
    status = db.Column(db.String(20), default="Available")
    photo1 = db.Column(db.String(200))
    photo2 = db.Column(db.String(200))


class Guest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    aadhaar = db.Column(db.String(12))


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guest_id = db.Column(db.Integer, db.ForeignKey("guest.id"))
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"))
    check_in = db.Column(db.String(20))
    check_out = db.Column(db.String(20))

    guest = db.relationship("Guest")
    room = db.relationship("Room")


class BookingRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guest_id = db.Column(db.Integer, db.ForeignKey("guest.id"))
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"))
    check_in = db.Column(db.String(20))
    check_out = db.Column(db.String(20))
    status = db.Column(db.String(20), default="Pending")
    requested_by = db.Column(db.String(50))

    guest = db.relationship("Guest")
    room = db.relationship("Room")


# ─── Initialize admin if missing ───────────────────────────────────────────────

@app.before_request
def ensure_admin():
    try:
        admin = User.query.filter_by(role="admin").first()
        if not admin:
            admin = User(username="admin", password="admin", role="admin")
            db.session.add(admin)
            db.session.commit()
    except:
        pass


# ─── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not username or not password:
            flash("Please enter both username and password")
            return render_template("login.html")

        if len(username) > 50 or len(password) > 50:
            flash("Invalid username or password length")
            return render_template("login.html")
        # If password is weak, show a warning but allow login attempt (don't block existing users)
        ok, msg = validate_password(password)
        if not ok:
            flash(f"Warning: password does not meet recommended strength ({msg})")

        user = User.query.filter_by(username=username, password=password).first()

        if user:
            session["user"] = user.username
            session["role"] = user.role

            if user.role == "admin":
                return redirect("/admin/dashboard")
            elif user.role == "user":
                return redirect("/user/dashboard")
            else:
                return redirect("/dashboard")
        else:
            flash("Invalid username or password")
            return render_template("login.html")

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        missing = require_fields(request.form, ["username", "password"])
        if missing:
            flash("Please fill all required fields")
            return render_template("signup.html")

        if len(username) > 50 or len(password) > 50:
            flash("Invalid username or password length")
            return render_template("signup.html")

        ok, msg = validate_password(password)
        if not ok:
            flash(f"Password must be strong: {msg}")
            return render_template("signup.html")

        if User.query.filter_by(username=username).first():
            flash("Username already exists")
            return render_template("signup.html")

        user = User(username=username, password=password, role="user")
        db.session.add(user)
        db.session.commit()

        session["user"] = user.username
        session["role"] = user.role
        return redirect("/user/dashboard")

    return render_template("signup.html")


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    total = len(room_cache)
    available = len(room_cache.get_available_rooms())
    booked = total - available

    booked_bookings = Booking.query.join(Room).filter(Room.status == "Booked").all()
    total_revenue = sum(
        booking.room.price * calculate_days(booking.check_in, booking.check_out)
        for booking in booked_bookings
    )

    pending_requests = BookingRequest.query.filter_by(status="Pending").all()

    return render_template(
        "dashboard.html",
        total=total,
        available=available,
        booked=booked,
        total_revenue=total_revenue,
        pending_requests=pending_requests
    )


@app.route("/admin/dashboard")
def admin_dashboard():
    if "user" not in session or session.get("role") != "admin":
        return redirect("/login")

    booked_bookings = Booking.query.join(Room).filter(Room.status == "Booked").all()
    total_revenue = sum(
        booking.room.price * calculate_days(booking.check_in, booking.check_out)
        for booking in booked_bookings
    )

    from datetime import datetime
    now = datetime.now()
    monthly_bookings = [
        b for b in Booking.query.all()
        if datetime.strptime(b.check_in, '%Y-%m-%d').month == now.month
        and datetime.strptime(b.check_in, '%Y-%m-%d').year == now.year
    ]
    total_guests_this_month = len(monthly_bookings)

    avg_booking_value = (total_revenue // len(booked_bookings)) if booked_bookings else 0

    total_rooms = len(room_cache)
    booked_rooms = total_rooms - len(room_cache.get_available_rooms())
    occupancy_rate = round((booked_rooms / total_rooms * 100) if total_rooms > 0 else 0, 1)

    return render_template(
        "admin_dashboard.html",
        total_revenue=total_revenue,
        total_guests_this_month=total_guests_this_month,
        avg_booking_value=avg_booking_value,
        occupancy_rate=occupancy_rate
    )


@app.route("/user/dashboard")
def user_dashboard():
    if "user" not in session or session.get("role") != "user":
        return redirect("/login")

    all_rooms = room_cache.to_list()
    available_rooms = room_cache.get_available_rooms().to_list()

    user_requests = BookingRequest.query.filter_by(requested_by=session["user"]).all()

    approved_requests = BookingRequest.query.filter_by(
        requested_by=session["user"], status="Approved"
    ).all()

    user_bookings = []
    for req in approved_requests:
        booking = Booking.query.filter_by(
            guest_id=req.guest_id,
            room_id=req.room_id,
            check_in=req.check_in,
            check_out=req.check_out
        ).first()
        if booking:
            days = calculate_days(booking.check_in, booking.check_out)
            booking.total_amount = booking.room.price * days
            user_bookings.append(booking)

    return render_template(
        "user_dashboard.html",
        all_rooms=all_rooms,
        available_rooms=available_rooms,
        user_requests=user_requests,
        user_bookings=user_bookings
    )


@app.route("/rooms", methods=["GET", "POST"])
def rooms():
    if request.method == "POST":
        missing = require_fields(request.form, ["room_number", "room_type", "price"])
        if missing:
            flash("Please fill all required fields for the room")
            return redirect("/rooms")

        price = safe_int(request.form.get("price"))
        if price is None or price < 0:
            flash("Invalid price")
            return redirect("/rooms")

        room = Room(
            room_number=request.form.get("room_number").strip(),
            room_type=request.form.get("room_type").strip(),
            price=price
        )
        db.session.add(room)
        db.session.commit()

    return render_template("rooms.html", rooms=room_cache.to_list())


@app.route("/guests", methods=["GET"])
def guests():
    if "user" not in session:
        return redirect("/login")
    bookings = Booking.query.all()
    return render_template("guests.html", bookings=bookings)


@app.route("/book", methods=["GET", "POST"])
def book():
    if "user" not in session:
        return redirect("/login")

    if request.method == "POST":
        missing = require_fields(request.form, ["name", "phone", "aadhaar", "room", "check_in", "check_out"])
        if missing:
            flash("Please fill all booking fields")
            return redirect("/book")

        if not is_valid_date(request.form.get("check_in")) or not is_valid_date(request.form.get("check_out")):
            flash("Invalid dates; use YYYY-MM-DD")
            return redirect("/book")

        room_id = safe_int(request.form.get("room"))
        if room_id is None:
            flash("Invalid room selection")
            return redirect("/book")

        guest = Guest(
            name=request.form.get("name").strip(),
            phone=request.form.get("phone").strip(),
            aadhaar=request.form.get("aadhaar").strip()
        )
        db.session.add(guest)
        db.session.flush()

        booking = Booking(
            guest_id=guest.id,
            room_id=room_id,
            check_in=request.form.get("check_in"),
            check_out=request.form.get("check_out")
        )

        room = room_cache.find_by_id(room_id)
        if room:
            room.status = "Booked"

        db.session.add(booking)
        db.session.commit()
        return redirect("/dashboard")

    return render_template(
        "book.html",
        available_rooms=room_cache.get_available_rooms().to_list(),
        all_rooms=room_cache.to_list()
    )


@app.route("/bookings", methods=["GET", "POST"])
def bookings():
    if request.method == "POST":
        missing = require_fields(request.form, ["guest", "room", "check_in", "check_out"])
        if missing:
            flash("Please fill all booking fields")
            return redirect("/bookings")

        room_id = safe_int(request.form.get("room"))
        guest_id = safe_int(request.form.get("guest"))
        if room_id is None or guest_id is None:
            flash("Invalid guest or room selection")
            return redirect("/bookings")

        if not is_valid_date(request.form.get("check_in")) or not is_valid_date(request.form.get("check_out")):
            flash("Invalid dates; use YYYY-MM-DD")
            return redirect("/bookings")

        booking = Booking(
            guest_id=guest_id,
            room_id=room_id,
            check_in=request.form.get("check_in"),
            check_out=request.form.get("check_out")
        )

        room = room_cache.find_by_id(room_id)
        if room:
            room.status = "Booked"

        db.session.add(booking)
        db.session.commit()

    return render_template(
        "bookings.html",
        guests=Guest.query.all(),
        rooms=room_cache.get_available_rooms().to_list(),
        bookings=Booking.query.all()
    )


@app.route("/admin/create-receptionist", methods=["POST"])
def create_receptionist():
    if "user" not in session or session.get("role") != "admin":
        return redirect("/login")

    missing = require_fields(request.form, ["username", "password"])
    if missing:
        flash("Please provide username and password for receptionist")
        return redirect("/admin/receptionists")

    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()

    if len(username) > 50 or len(password) > 50:
        flash("Invalid username or password length")
        return redirect("/admin/receptionists")

    if User.query.filter_by(username=username).first():
        flash("Username already exists")
        return redirect("/admin/receptionists")

    receptionist = User(username=username, password=password, role="receptionist")
    db.session.add(receptionist)
    db.session.commit()

    return redirect("/admin/receptionists")


@app.route("/admin/rooms", methods=["GET", "POST"])
def admin_rooms():
    if "user" not in session or session.get("role") != "admin":
        return redirect("/login")

    if request.method == "POST":
        room_number = request.form["room_number"]
        if room_cache.find_by_number(room_number):
            flash("Room number already exists. Please use a different room number.")
            return redirect("/admin/rooms")

        room = Room(
            room_number=room_number,
            room_type=request.form["room_type"],
            price=request.form["price"]
        )
        db.session.add(room)
        db.session.commit()
        flash("Room created successfully!")
        return redirect("/admin/rooms")

    return render_template("admin_rooms.html", rooms=room_cache.to_list())


@app.route("/admin/receptionists")
def admin_receptionists():
    if "user" not in session or session.get("role") != "admin":
        return redirect("/login")
    receptionists = User.query.filter_by(role="receptionist").all()
    return render_template("admin_receptionists.html", receptionists=receptionists)


@app.route("/admin/history")
def admin_history():
    if "user" not in session or session.get("role") != "admin":
        return redirect("/login")
    return render_template(
        "admin_history.html",
        bookings=Booking.query.all(),
        rooms=room_cache.to_list()
    )


@app.route("/admin/delete-receptionist/<int:receptionist_id>")
def delete_receptionist(receptionist_id):
    if "user" not in session or session.get("role") != "admin":
        return redirect("/login")

    receptionist = User.query.get(receptionist_id)
    if receptionist and receptionist.role == "receptionist":
        db.session.delete(receptionist)
        db.session.commit()

    return redirect("/admin/receptionists")


@app.route("/admin/delete-room/<int:room_id>")
def delete_room(room_id):
    if "user" not in session or session.get("role") != "admin":
        return redirect("/login")

    room = Room.query.get(room_id)
    if room:
        db.session.delete(room)
        db.session.commit()

    return redirect("/admin/rooms")


@app.route("/admin/upload-photos/<int:room_id>", methods=["GET", "POST"])
def upload_photos(room_id):
    if "user" not in session or session.get("role") not in ["admin", "receptionist"]:
        return redirect("/login")

    room = room_cache.find_by_id(room_id)
    if not room:
        room = Room.query.get(room_id)
        if not room:
            return redirect("/admin/rooms")

    if request.method == "POST":
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

        if "photo1" in request.files and request.files["photo1"].filename:
            file = request.files["photo1"]
            if not allowed_file(file.filename):
                flash("photo1: invalid file type")
                return redirect(f"/admin/upload-photos/{room_id}")
            filename = secure_filename(f"room_{room.room_number}_1.{file.filename.rsplit('.',1)[-1]}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            room.photo1 = filename

        if "photo2" in request.files and request.files["photo2"].filename:
            file = request.files["photo2"]
            if not allowed_file(file.filename):
                flash("photo2: invalid file type")
                return redirect(f"/admin/upload-photos/{room_id}")
            filename = secure_filename(f"room_{room.room_number}_2.{file.filename.rsplit('.',1)[-1]}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            room.photo2 = filename

        db.session.commit()
        flash("Photos uploaded successfully!")
        return redirect("/admin/rooms")

    return render_template("upload_photos.html", room=room)


@app.route("/bill/<int:booking_id>")
def generate_bill(booking_id):
    if "user" not in session:
        return redirect("/login")

    booking = Booking.query.get(booking_id)
    if not booking:
        if session.get("role") == "user":
            return redirect("/user/dashboard")
        return redirect("/guests")

    if session.get("role") == "user":
        req = BookingRequest.query.filter_by(
            guest_id=booking.guest_id,
            room_id=booking.room_id,
            requested_by=session["user"],
            status="Approved"
        ).first()
        if not req:
            return redirect("/user/dashboard")

    # Ensure booking date fields are datetime objects for template `.strftime` usage
    try:
        if isinstance(booking.check_in, str) and is_valid_date(booking.check_in):
            booking.check_in = datetime.strptime(booking.check_in, '%Y-%m-%d')
    except Exception:
        pass

    try:
        if isinstance(booking.check_out, str) and is_valid_date(booking.check_out):
            booking.check_out = datetime.strptime(booking.check_out, '%Y-%m-%d')
    except Exception:
        pass

    # Attach a created_at datetime for invoices if not present
    if not getattr(booking, 'created_at', None):
        booking.created_at = datetime.now()
    else:
        try:
            if isinstance(booking.created_at, str) and booking.created_at:
                booking.created_at = datetime.strptime(booking.created_at, '%Y-%m-%d %H:%M:%S')
        except Exception:
            # leave as-is
            pass

    days = calculate_days(booking.check_in, booking.check_out)
    total_amount = booking.room.price * days

    return render_template("bill.html", booking=booking, days=days, total_amount=total_amount)


@app.route("/user/book/<int:room_id>", methods=["GET", "POST"])
def user_book(room_id):
    if "user" not in session or session.get("role") != "user":
        return redirect("/login")

    room = room_cache.find_by_id(room_id)
    if not room or room.status != "Available":
        return redirect("/user/dashboard")

    if request.method == "POST":
        guest = Guest(
            name=request.form["name"],
            phone=request.form["phone"],
            aadhaar=request.form["aadhaar"]
        )
        db.session.add(guest)
        db.session.flush()

        req = BookingRequest(
            guest_id=guest.id,
            room_id=room_id,
            check_in=request.form["check_in"],
            check_out=request.form["check_out"],
            requested_by=session["user"]
        )
        db.session.add(req)
        db.session.commit()

        flash("Booking request submitted! Please wait for confirmation.")
        return redirect("/user/dashboard")

    return render_template("user_book.html", room=room)


@app.route("/approve-request/<int:request_id>")
def approve_request(request_id):
    if "user" not in session or session.get("role") not in ["admin", "receptionist"]:
        return redirect("/login")

    req = BookingRequest.query.get(request_id)
    if req and req.status == "Pending":
        booking = Booking(
            guest_id=req.guest_id,
            room_id=req.room_id,
            check_in=req.check_in,
            check_out=req.check_out
        )

        room = room_cache.find_by_id(req.room_id)
        if room:
            room.status = "Booked"

        req.status = "Approved"
        db.session.add(booking)
        db.session.commit()

    return redirect("/dashboard")


@app.route("/reject-request/<int:request_id>")
def reject_request(request_id):
    if "user" not in session or session.get("role") not in ["admin", "receptionist"]:
        return redirect("/login")

    req = BookingRequest.query.get(request_id)
    if req and req.status == "Pending":
        req.status = "Rejected"
        db.session.commit()

    return redirect("/dashboard")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)  
