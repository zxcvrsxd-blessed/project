import os
import re
import uuid
import unicodedata
from io import BytesIO
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from PIL import Image, ImageOps
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
FULL_IMAGE_MAX_BYTES = 4 * 1024 * 1024
CARD_IMAGE_MAX_BYTES = 512 * 1024
CARD_IMAGE_WIDTHS = (240, 360, 540)
FULL_IMAGE_MAX_WIDTH = 2560

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-this-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///zoo_store.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
app.config["CURRENCY_CODE"] = "BYN"

db = SQLAlchemy(app)
_categories_cache = None


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    products = db.relationship("Product", backref="category", lazy=True)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    price = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)
    in_stock = db.Column(db.Boolean, default=True, nullable=False)
    is_popular = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    text = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if current_user() is None:
            flash("Войдите в аккаунт, чтобы продолжить.", "warning")
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = current_user()
        if user is None:
            flash("Войдите в аккаунт администратора.", "warning")
            return redirect(url_for("admin_login", next=request.path))
        if not user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped_view


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def normalize_image(image):
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    return image


def resized_copy(image, max_width):
    resized = image.copy()
    if resized.width > max_width:
        ratio = max_width / float(resized.width)
        height = max(1, int(resized.height * ratio))
        resized = resized.resize((max_width, height), Image.Resampling.LANCZOS)
    return resized


def encode_webp(image, destination, max_bytes):
    candidate = image
    last_bytes = None
    for _ in range(12):
        quality = 88
        while quality >= 36:
            buffer = BytesIO()
            candidate.save(buffer, format="WEBP", quality=quality, method=6, optimize=True)
            last_bytes = buffer.getvalue()
            if buffer.tell() <= max_bytes:
                destination.write_bytes(last_bytes)
                return
            quality -= 6

        next_width = max(96, int(candidate.width * 0.84))
        if next_width >= candidate.width:
            break
        candidate = resized_copy(candidate, next_width)

    if last_bytes is not None:
        destination.write_bytes(last_bytes)


def variant_filename(filename, width):
    stem = Path(filename).stem
    return f"{stem}_{width}w.webp"


def ensure_image_variants(filename):
    source_path = UPLOAD_DIR / filename
    if not source_path.exists():
        return

    with Image.open(source_path) as source:
        image = normalize_image(source)
        for width in CARD_IMAGE_WIDTHS:
            destination = UPLOAD_DIR / variant_filename(filename, width)
            if destination.exists():
                continue
            variant = resized_copy(image, width)
            encode_webp(variant, destination, CARD_IMAGE_MAX_BYTES)


def save_image(file_storage):
    if not file_storage or file_storage.filename == "":
        return None
    if not allowed_image(file_storage.filename):
        raise ValueError("Можно загружать только JPG, PNG или WEBP.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.webp"
    destination = UPLOAD_DIR / filename

    with Image.open(file_storage.stream) as source:
        image = normalize_image(source)
        optimized = resized_copy(image, FULL_IMAGE_MAX_WIDTH)
        encode_webp(optimized, destination, FULL_IMAGE_MAX_BYTES)

    ensure_image_variants(filename)
    return filename


def product_image_url(product):
    if product.image_filename:
        return url_for("static", filename=f"uploads/{product.image_filename}")
    return url_for("static", filename="img/product-placeholder.svg")


def product_card_image_attrs(product):
    if not product.image_filename:
        placeholder_url = url_for("static", filename="img/product-placeholder.svg")
        return {
            "src": placeholder_url,
            "srcset": "",
            "sizes": "",
        }

    ensure_image_variants(product.image_filename)
    src = product_image_url(product)
    srcset_parts = []
    for width in CARD_IMAGE_WIDTHS:
        variant_path = UPLOAD_DIR / variant_filename(product.image_filename, width)
        if variant_path.exists():
            srcset_parts.append(f"{url_for('static', filename=f'uploads/{variant_path.name}')} {width}w")

    return {
        "src": src,
        "srcset": ", ".join(srcset_parts),
        "sizes": "(max-width: 620px) 100vw, (max-width: 860px) 50vw, 33vw",
    }


def announcement_image_url(announcement):
    if announcement.image_filename:
        return url_for("static", filename=f"uploads/{announcement.image_filename}")
    return url_for("static", filename="img/news-placeholder.svg")


def get_navigation_categories():
    global _categories_cache
    if _categories_cache is None:
        _categories_cache = Category.query.order_by(Category.name.asc()).all()
    return _categories_cache


def invalidate_categories_cache():
    global _categories_cache
    _categories_cache = None


@app.context_processor
def inject_layout_data():
    site_url = os.getenv("SITE_URL", "https://любимый-хвостик.рф")
    telegram_url = os.getenv("TELEGRAM_URL", "").strip()
    return {
        "current_user": current_user(),
        "categories": get_navigation_categories(),
        "product_image_url": product_image_url,
        "product_card_image_attrs": product_card_image_attrs,
        "announcement_image_url": announcement_image_url,
        "site_url": site_url,
        "telegram_url": telegram_url,
        "currency_code": app.config["CURRENCY_CODE"],
    }


@app.route("/")
def index():
    query = request.args.get("q", "").strip()
    if query:
        return redirect(url_for("catalog", q=query))

    popular_products = Product.query.filter_by(is_popular=True).order_by(Product.created_at.desc()).limit(4).all()
    latest_products = Product.query.order_by(Product.created_at.desc()).limit(4).all()
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).limit(3).all()
    return render_template(
        "index.html",
        popular_products=popular_products,
        latest_products=latest_products,
        announcements=announcements,
    )


@app.route("/catalog")
def catalog():
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 9
    query_text = request.args.get("q", "").strip()
    category_slug = request.args.get("category", "").strip()
    min_price = request.args.get("min_price", type=float)
    max_price = request.args.get("max_price", type=float)
    sort = request.args.get("sort", "new")

    query = Product.query.join(Category)
    if query_text:
        like = f"%{query_text}%"
        query = query.filter(or_(Product.name.ilike(like), Product.description.ilike(like), Category.name.ilike(like)))
    if category_slug:
        query = query.filter(Category.slug == category_slug)
    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)

    if sort == "price_asc":
        query = query.order_by(Product.price.asc())
    elif sort == "price_desc":
        query = query.order_by(Product.price.desc())
    elif sort == "name":
        query = query.order_by(Product.name.asc())
    else:
        query = query.order_by(Product.created_at.desc())

    total = query.count()
    products = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = max((total + per_page - 1) // per_page, 1)
    return render_template(
        "catalog.html",
        products=products,
        total=total,
        page=page,
        pages=pages,
        filters={
            "q": query_text,
            "category": category_slug,
            "min_price": min_price,
            "max_price": max_price,
            "sort": sort,
        },
    )


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    return render_template("product.html", product=product)


@app.route("/announcements")
def announcements():
    items = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template("announcements.html", announcements=items)


@app.route("/announcements/<int:announcement_id>")
def announcement_detail(announcement_id):
    announcement = db.session.get(Announcement, announcement_id)
    if announcement is None:
        abort(404)
    return render_template("announcement_detail.html", announcement=announcement)


@app.route("/contacts")
def contacts():
    return render_template("contacts.html")


@app.route("/telegram")
def telegram_redirect():
    telegram_url = os.getenv("TELEGRAM_URL", "").strip()
    if not telegram_url:
        flash("Ссылка на Telegram еще не настроена.", "warning")
        return redirect(url_for("contacts"))
    return redirect(telegram_url)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user is None or not user.is_admin or not user.check_password(password):
            flash("Неверный логин администратора или пароль.", "danger")
            return render_template("login.html")

        session["user_id"] = user.id
        flash("Вы вошли в админку.", "success")
        return redirect(request.args.get("next") or url_for("admin_dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    products = Product.query.order_by(Product.created_at.desc()).all()
    categories = Category.query.order_by(Category.name.asc()).all()
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template(
        "admin/dashboard.html",
        products=products,
        categories=categories,
        announcements=announcements,
    )


@app.route("/admin/categories/new", methods=["GET", "POST"])
@admin_required
def admin_category_create():
    category = Category()
    if request.method == "POST":
        if save_category_form(category):
            flash("Категория добавлена.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("admin/category_form.html", category=category, title="Новая категория")


@app.route("/admin/categories/<int:category_id>/delete", methods=["POST"])
@admin_required
def admin_category_delete(category_id):
    category = db.session.get(Category, category_id)
    if category is None:
        abort(404)
    if category.products:
        flash("Нельзя удалить категорию, пока в ней есть товары.", "warning")
        return redirect(url_for("admin_dashboard"))

    db.session.delete(category)
    db.session.commit()
    invalidate_categories_cache()
    flash("Категория удалена.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/products/new", methods=["GET", "POST"])
@admin_required
def admin_product_create():
    product = Product(in_stock=True, is_popular=False)
    if request.method == "POST":
        if save_product_form(product):
            flash("Товар добавлен.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("admin/product_form.html", product=product, title="Новый товар")


@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_product_edit(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    if request.method == "POST":
        if save_product_form(product):
            flash("Товар обновлен.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("admin/product_form.html", product=product, title="Редактирование товара")


@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
@admin_required
def admin_product_delete(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    db.session.delete(product)
    db.session.commit()
    flash("Товар удален.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/announcements/new", methods=["GET", "POST"])
@admin_required
def admin_announcement_create():
    announcement = Announcement()
    if request.method == "POST":
        if save_announcement_form(announcement):
            flash("Объявление опубликовано.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("admin/announcement_form.html", announcement=announcement, title="Новое объявление")


@app.route("/admin/announcements/<int:announcement_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_announcement_edit(announcement_id):
    announcement = db.session.get(Announcement, announcement_id)
    if announcement is None:
        abort(404)
    if request.method == "POST":
        if save_announcement_form(announcement):
            flash("Объявление обновлено.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("admin/announcement_form.html", announcement=announcement, title="Редактирование объявления")


@app.route("/admin/announcements/<int:announcement_id>/delete", methods=["POST"])
@admin_required
def admin_announcement_delete(announcement_id):
    announcement = db.session.get(Announcement, announcement_id)
    if announcement is None:
        abort(404)
    db.session.delete(announcement)
    db.session.commit()
    flash("Объявление удалено.", "success")
    return redirect(url_for("admin_dashboard"))


def save_product_form(product):
    try:
        product.name = request.form.get("name", "").strip()
        product.category_id = request.form.get("category_id", type=int)
        product.price = request.form.get("price", type=float) or 0
        product.description = request.form.get("description", "").strip()
        product.in_stock = request.form.get("in_stock") == "on"
        product.is_popular = request.form.get("is_popular") == "on"

        if not product.name or not product.category_id or not product.description:
            flash("Заполните название, категорию и описание.", "danger")
            return False

        image_filename = save_image(request.files.get("image"))
        if image_filename:
            product.image_filename = image_filename

        db.session.add(product)
        db.session.commit()
        return True
    except ValueError as error:
        flash(str(error), "danger")
        return False


def make_category_slug(value):
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    if slug:
        return slug
    return f"category-{uuid.uuid4().hex[:8]}"


def save_category_form(category):
    name = request.form.get("name", "").strip()
    slug = make_category_slug(name)

    if not name:
        flash("Укажите название категории.", "danger")
        return False

    duplicate_name = Category.query.filter(Category.name.ilike(name)).first()
    if duplicate_name is not None:
        flash("Категория с таким названием уже существует.", "danger")
        return False

    duplicate_slug = Category.query.filter_by(slug=slug).first()
    if duplicate_slug is not None:
        flash("Не удалось создать технический идентификатор категории. Попробуйте другое название.", "danger")
        return False

    category.name = name
    category.slug = slug
    db.session.add(category)
    db.session.commit()
    invalidate_categories_cache()
    return True


def save_announcement_form(announcement):
    try:
        announcement.title = request.form.get("title", "").strip()
        announcement.text = request.form.get("text", "").strip()
        if not announcement.title or not announcement.text:
            flash("Заполните заголовок и текст.", "danger")
            return False

        image_filename = save_image(request.files.get("image"))
        if image_filename:
            announcement.image_filename = image_filename

        db.session.add(announcement)
        db.session.commit()
        return True
    except ValueError as error:
        flash(str(error), "danger")
        return False


def seed_defaults():
    categories = [
        ("Корм", "food"),
        ("Игрушки", "toys"),
        ("Аксессуары", "accessories"),
        ("Для кошек", "cats"),
        ("Для собак", "dogs"),
        ("Птицы и грызуны", "small-pets"),
    ]
    for name, slug in categories:
        if Category.query.filter_by(slug=slug).first() is None:
            db.session.add(Category(name=name, slug=slug))
    db.session.commit()
    invalidate_categories_cache()

    if User.query.filter_by(role="admin").first() is None:
        admin = User(username="admin", email="admin@example.com", role="admin")
        admin.set_password(os.getenv("ADMIN_PASSWORD", "admin123"))
        db.session.add(admin)

    if Product.query.count() == 0:
        food = Category.query.filter_by(slug="food").first()
        cats = Category.query.filter_by(slug="cats").first()
        dogs = Category.query.filter_by(slug="dogs").first()
        toys = Category.query.filter_by(slug="toys").first()
        sample_products = [
            Product(
                name="Сухой корм для кошек",
                category=food,
                price=45,
                description="Полнорационный корм для взрослых кошек с высоким содержанием белка.",
                image_filename="cat-food.jpg",
                in_stock=True,
                is_popular=True,
            ),
            Product(
                name="Игрушка-мяч для собак",
                category=toys,
                price=14,
                description="Прочная игрушка для активных прогулок и тренировок.",
                image_filename="dog-toy.jpg",
                in_stock=True,
                is_popular=True,
            ),
            Product(
                name="Лежанка мягкая",
                category=dogs,
                price=89,
                description="Уютная лежанка со съемным чехлом для собак и кошек.",
                image_filename="dog-bed.jpg",
                in_stock=True,
                is_popular=False,
            ),
            Product(
                name="Когтеточка настольная",
                category=cats,
                price=35,
                description="Компактная когтеточка для ухода за когтями и защиты мебели.",
                image_filename="scratching-post.jpg",
                in_stock=True,
                is_popular=True,
            ),
        ]
        db.session.add_all(sample_products)
    else:
        example_images = {
            "Сухой корм для кошек": "cat-food.jpg",
            "Игрушка-мяч для собак": "dog-toy.jpg",
            "Лежанка мягкая": "dog-bed.jpg",
            "Когтеточка настольная": "scratching-post.jpg",
        }
        for product in Product.query.all():
            if not product.image_filename and product.name in example_images:
                product.image_filename = example_images[product.name]

    if Announcement.query.count() == 0:
        db.session.add(
            Announcement(
                title="Весенняя акция на товары для питомцев",
                text="Скидки на корма, игрушки и аксессуары. Следите за обновлениями каталога.",
            )
        )

    db.session.commit()


with app.app_context():
    db.create_all()
    seed_defaults()


if __name__ == "__main__":
    app.run(debug=True, port="5003")
