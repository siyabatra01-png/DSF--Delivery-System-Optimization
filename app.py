from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from pymongo import MongoClient
from datetime import datetime, timedelta
from functools import wraps
import math
import json
import urllib
import os
from collections import defaultdict

app = Flask(__name__)

# ====================== SSMS SQL SERVER CONFIG ======================
params = urllib.parse.quote_plus(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=localhost;'
    'DATABASE=FoodExpressDB;'
    'Trusted_Connection=yes;'
)
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc:///?odbc_connect={params}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'foodexpress_secured_matrix_key_token_2026'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=45)

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# ====================== MONGODB (Dual-Persistence Only) ======================
try:
    mongo_client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=3000)
    mongo_db = mongo_client["foodexpress_analytics_db"]
    orders_log = mongo_db["unstructured_orders"]
    feedbacks_col = mongo_db["customer_feedbacks"]
    mongo_client.server_info()
except:
    class MockCollection:
        def insert_one(self, doc): pass
        def find(self, filter=None): return []
        def update_one(self, filter, update): pass
        def count_documents(self, filter=None): return 0
    orders_log = MockCollection()
    feedbacks_col = MockCollection()
    mongo_db = {"unstructured_orders": orders_log, "customer_feedbacks": feedbacks_col}

# ====================== VENDOR DATA (In-Memory Restaurant Catalog) ======================
VENDORS = {
    "Desi Cuisine": {
        "Karachi Biryani House": {
            "hours": "11:00 AM - 11:00 PM",
            "available": True,
            "lat": 24.8732, "lng": 67.0682,
            "menu": [
                {"id": "kb01", "name": "Chicken Biryani", "price": 450, "rating": 4.8, "image": "https://images.unsplash.com/photo-1589301760014-d929f3979dbc?w=200"},
                {"id": "kb02", "name": "Mutton Karahi", "price": 750, "rating": 4.7, "image": "https://images.unsplash.com/photo-1631515243349-e0cb75fb8d3a?w=200"},
                {"id": "kb03", "name": "Dal Chawal", "price": 250, "rating": 4.5, "image": "https://images.unsplash.com/photo-1546833999-b9f581a1996d?w=200"},
            ]
        },
        "Bundoo Khan": {
            "hours": "12:00 PM - 12:00 AM",
            "available": True,
            "lat": 24.8601, "lng": 67.0551,
            "menu": [
                {"id": "bk01", "name": "Seekh Kebab Platter", "price": 600, "rating": 4.9, "image": "https://images.unsplash.com/photo-1529563021893-cc83c992d75d?w=200"},
                {"id": "bk02", "name": "Nihari", "price": 500, "rating": 4.8, "image": "https://images.unsplash.com/photo-1631515243349-e0cb75fb8d3a?w=200"},
            ]
        }
    },
    "Fast Food": {
        "Burger Barn": {
            "hours": "10:00 AM - 2:00 AM",
            "available": True,
            "lat": 24.8500, "lng": 67.0100,
            "menu": [
                {"id": "bb01", "name": "Classic Smash Burger", "price": 550, "rating": 4.6, "image": "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=200"},
                {"id": "bb02", "name": "Crispy Chicken Sandwich", "price": 480, "rating": 4.5, "image": "https://images.unsplash.com/photo-1606755962773-d324e0a13086?w=200"},
                {"id": "bb03", "name": "Loaded Cheese Fries", "price": 280, "rating": 4.4, "image": "https://images.unsplash.com/photo-1573080496219-bb080dd4f877?w=200"},
            ]
        },
        "Pizza Nexus": {
            "hours": "11:00 AM - 1:00 AM",
            "available": True,
            "lat": 24.8620, "lng": 67.0400,
            "menu": [
                {"id": "pn01", "name": "Pepperoni Pizza (Large)", "price": 900, "rating": 4.7, "image": "https://images.unsplash.com/photo-1565299624946-b28f40a0ae38?w=200"},
                {"id": "pn02", "name": "BBQ Chicken Pizza", "price": 850, "rating": 4.6, "image": "https://images.unsplash.com/photo-1513104890138-7c749659a591?w=200"},
            ]
        }
    },
    "Cafes & Coffee": {
        "Brew & Bites": {
            "hours": "8:00 AM - 11:00 PM",
            "available": True,
            "lat": 24.8450, "lng": 67.0200,
            "menu": [
                {"id": "bb_c01", "name": "Caramel Latte", "price": 350, "rating": 4.8, "image": "https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=200"},
                {"id": "bb_c02", "name": "Avocado Toast", "price": 420, "rating": 4.6, "image": "https://images.unsplash.com/photo-1541519227354-08fa5d50c820?w=200"},
                {"id": "bb_c03", "name": "Chocolate Brownie", "price": 220, "rating": 4.9, "image": "https://images.unsplash.com/photo-1606313564200-e75d5e30476c?w=200"},
            ]
        }
    }
}

def get_item_by_id(item_id):
    for category, restaurants in VENDORS.items():
        for rest_name, details in restaurants.items():
            for item in details['menu']:
                if item['id'] == item_id:
                    return item, rest_name, category
    return None, None, None

ORDER_STATUSES = ['Placed', 'Preparing', 'Dispatched', 'On the Way', 'Delivered', 'Cancelled']

# ====================== AUTH DECORATORS ======================
def login_required(role=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('username'):
                flash("Please sign in first.", "error")
                return redirect(url_for('login_page'))
            if role and session.get('role') != role:
                return "Unauthorized", 403
            return func(*args, **kwargs)
        return wrapper
    return decorator

# ====================== MODELS ======================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    contact = db.Column(db.String(20), unique=True, nullable=False)
    age = db.Column(db.Integer)
    address = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='Active')
    is_online = db.Column(db.Boolean, default=False)
    lat = db.Column(db.Float, default=24.8607)
    lng = db.Column(db.Float, default=67.0011)

class ActiveOrder(db.Model):
    __tablename__ = 'active_orders'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    restaurant_name = db.Column(db.String(100), nullable=False)
    cuisine = db.Column(db.String(50), nullable=False)
    items_summary = db.Column(db.Text, nullable=False)
    total_cost = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='Placed')
    rider_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship('User', backref=db.backref('orders', lazy=True))

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    role_target = db.Column(db.String(30), nullable=True)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(30), default='info')
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def send_notification(message, category='info', user_id=None, role_target=None):
    try:
        notif = Notification(user_id=user_id, role_target=role_target, message=message, category=category)
        db.session.add(notif)
        db.session.commit()
    except Exception as e:
        print(f"Notification error: {e}")

# ====================== GEOLOCATION & ALGORITHMS ======================
def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def assign_best_rider(restaurant_lat, restaurant_lng):
    try:
        online_riders = User.query.filter_by(role='delivery_partner', status='Active', is_online=True).all()
        best_rider_id = None
        best_score = float('inf')
        for rider in online_riders:
            distance = calculate_haversine_distance(restaurant_lat, restaurant_lng, rider.lat, rider.lng)
            active_load = ActiveOrder.query.filter(
                ActiveOrder.rider_id == rider.id,
                ActiveOrder.status.in_(['Placed', 'Preparing', 'Dispatched', 'On the Way'])
            ).count()
            score = distance + (active_load * 3)
            if score < best_score:
                best_score = score
                best_rider_id = rider.id
        return best_rider_id
    except:
        return None

@app.context_processor
def inject_user_context():
    return dict(session_user=session.get('username'), session_role=session.get('role'), system_issues=[])

# ====================== ROUTES ======================

@app.route('/')
def home():
    return render_template('index.html', vendors=VENDORS)

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register_page():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role', 'customer')
        name = request.form.get('name')
        email = request.form.get('email')
        contact = request.form.get('contact')
        address = request.form.get('address')
        age = request.form.get('age')

        if User.query.filter_by(username=username).first():
            flash("Username already exists!", "error")
            return redirect(url_for('register_page'))

        if User.query.filter_by(email=email).first():
            flash("Email already registered!", "error")
            return redirect(url_for('register_page'))

        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        user_status = 'Pending' if role == 'delivery_partner' else 'Active'

        new_user = User(username=username, password_hash=hashed, role=role, name=name, email=email,
                        contact=contact, age=int(age) if age else None, address=address, status=user_status)
        db.session.add(new_user)
        db.session.commit()
        flash("Registration successful! Please login.", "success")
        return redirect(url_for('login_page'))
    return render_template('register.html')

@app.route('/auth/login', methods=['POST'])
def handle_login():
    username = request.form.get('username')
    password = request.form.get('password')

    if username == 'admin' and password == '1234':
        session.clear()
        session['username'] = 'admin'
        session['role'] = 'admin'
        return redirect(url_for('admin_hub'))

    user = User.query.filter_by(username=username).first()
    if user and bcrypt.check_password_hash(user.password_hash, password):
        if user.status == 'Pending':
            flash("Your account is pending approval.", "error")
            return redirect(url_for('login_page'))
        if user.status == 'Inactive':
            flash("Your account has been deactivated. Contact support.", "error")
            return redirect(url_for('login_page'))

        session.clear()
        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = user.role
        session.permanent = True

        if user.role == 'delivery_partner':
            return redirect(url_for('rider_hub'))
        return redirect(url_for('customer_hub'))

    flash("Invalid credentials", "error")
    return redirect(url_for('login_page'))

# ====================== CUSTOMER ROUTES ======================

@app.route('/customer')
@login_required('customer')
def customer_hub():
    user = User.query.get(session.get('user_id'))
    recent_orders = ActiveOrder.query.filter_by(customer_id=user.id).order_by(ActiveOrder.created_at.desc()).limit(5).all()
    return render_template('customer.html', user=user, recent_orders=recent_orders)

@app.route('/customer/profile', methods=['GET', 'POST'])
@login_required('customer')
def customer_profile():
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        user.name = request.form.get('name')
        user.email = request.form.get('email')
        user.contact = request.form.get('contact')
        user.address = request.form.get('address')
        age_input = request.form.get('age')
        user.age = int(age_input) if age_input else None
        db.session.commit()
        flash("Profile updated successfully!", "success")
        return redirect(url_for('customer_profile'))
    order_history = ActiveOrder.query.filter_by(customer_id=user.id).order_by(ActiveOrder.created_at.desc()).all()
    return render_template('profile.html', user=user, order_history=order_history)

@app.route('/orders')
@login_required('customer')
def order_history():
    orders = ActiveOrder.query.filter_by(customer_id=session['user_id']).order_by(ActiveOrder.created_at.desc()).all()
    return render_template('orders.html', orders=orders)

@app.route('/order/cancel/<int:order_id>', methods=['POST'])
@login_required('customer')
def cancel_order(order_id):
    order = ActiveOrder.query.get_or_404(order_id)
    if order.customer_id != session.get('user_id'):
        return "Unauthorized", 403
    if order.status in ['Placed', 'Preparing']:
        order.status = 'Cancelled'
        db.session.commit()
        flash(f"Order #{order_id} has been cancelled.", "success")
        send_notification(
            f"⚠️ Order #{order_id} was cancelled by customer.",
            category='warning', role_target='admin'
        )
    else:
        flash("Order cannot be cancelled at this stage.", "error")
    return redirect(url_for('order_history'))

@app.route('/menu')
@login_required('customer')
def menu_page():
    return render_template('menu.html', vendors=VENDORS)

@app.route('/cart')
@login_required('customer')
def cart_page():
    cart = session.get('cart', [])
    total_amount = sum(i['price'] for i in cart)
    return render_template('cart.html', cart_items=cart, total_amount=total_amount)

@app.route('/add_to_cart', methods=['POST'])
@login_required('customer')
def add_to_cart():
    item_id = request.form.get('item_id')
    restaurant = request.form.get('restaurant')
    item, rest_name, category = get_item_by_id(item_id)
    if not item:
        flash("Item not found.", "error")
        return redirect(url_for('menu_page'))
    cart = session.get('cart', [])
    if cart and cart[0]['vendor'] != restaurant:
        flash("Your cart contains items from a different restaurant. Clear your cart first.", "error")
        return redirect(url_for('menu_page'))
    cart.append({
        'id': item['id'],
        'name': item['name'],
        'price': item['price'],
        'vendor': rest_name,
        'category': category,
        'image': item['image']
    })
    session['cart'] = cart
    flash(f"{item['name']} added to cart!", "success")
    return redirect(url_for('menu_page'))

@app.route('/cart/remove/<int:index>', methods=['POST'])
@login_required('customer')
def remove_cart_item(index):
    cart = session.get('cart', [])
    if 0 <= index < len(cart):
        removed = cart.pop(index)
        session['cart'] = cart
        flash(f"{removed['name']} removed from cart.", "success")
    return redirect(url_for('cart_page'))

@app.route('/order/checkout', methods=['POST'])
@login_required('customer')
def process_checkout():
    cart = session.get('cart', [])
    if not cart:
        flash("Your cart is empty.", "error")
        return redirect(url_for('menu_page'))

    target_vendor = cart[0]['vendor']
    target_cuisine = cart[0]['category']
    total_cost = sum(i['price'] for i in cart)
    items_summary = ", ".join(i['name'] for i in cart)

    rest_lat, rest_lng = 24.8607, 67.0011
    if target_vendor in VENDORS.get(target_cuisine, {}):
        rest_lat = VENDORS[target_cuisine][target_vendor].get('lat', 24.8607)
        rest_lng = VENDORS[target_cuisine][target_vendor].get('lng', 67.0011)

    new_order = ActiveOrder(
        customer_id=session.get('user_id'), restaurant_name=target_vendor,
        cuisine=target_cuisine, items_summary=items_summary, total_cost=total_cost, status='Placed'
    )
    db.session.add(new_order)
    db.session.commit()

    assigned_rider_id = assign_best_rider(rest_lat, rest_lng)
    if assigned_rider_id:
        new_order.rider_id = assigned_rider_id
        db.session.commit()

    try:
        orders_log.insert_one({
            "sql_order_id": new_order.id,
            "customer_id": session.get('user_id'),
            "restaurant_name": target_vendor,
            "cuisine": target_cuisine,
            "status": "Placed",
            "timestamp": datetime.utcnow()
        })
    except:
        pass

    session.pop('cart', None)
    flash(f"Order #{new_order.id} confirmed!", "success")

    send_notification(
        f"✅ Order #{new_order.id} confirmed! Your food from {target_vendor} is being prepared.",
        category='success', user_id=session.get('user_id')
    )
    if assigned_rider_id:
        send_notification(
            f"🛵 New delivery assignment! Order #{new_order.id} from {target_vendor}.",
            category='info', user_id=assigned_rider_id
        )
    send_notification(
        f"📦 New order #{new_order.id} placed from {target_vendor}.",
        category='info', role_target='admin'
    )

    return render_template('place_orders.html', order=new_order)

@app.route('/track/<int:order_id>')
@login_required('customer')
def tracking_dashboard(order_id):
    order = ActiveOrder.query.get_or_404(order_id)
    if session.get('role') == 'customer' and order.customer_id != session.get('user_id'):
        return "Unauthorized", 403
    if session.get('role') == 'delivery_partner' and order.rider_id != session.get('user_id'):
        return "Unauthorized", 403
    return render_template('tracking.html', order=order)

@app.route('/recommendations')
@login_required('customer')
def recommendations_page():
    user_id = session.get('user_id')
    past_orders = ActiveOrder.query.filter_by(customer_id=user_id).all()
    preferred_cuisines = set(o.cuisine for o in past_orders)

    recs = []
    seen_ids = set()

    for cuisine in (preferred_cuisines or VENDORS.keys()):
        if cuisine not in VENDORS:
            continue
        for rest_name, details in VENDORS[cuisine].items():
            for item in sorted(details['menu'], key=lambda x: x['rating'], reverse=True):
                if item['id'] not in seen_ids:
                    recs.append({'item': item, 'restaurant': rest_name, 'cuisine': cuisine})
                    seen_ids.add(item['id'])

    if len(recs) < 6:
        for cuisine, restaurants in VENDORS.items():
            for rest_name, details in restaurants.items():
                for item in details['menu']:
                    if item['id'] not in seen_ids:
                        recs.append({'item': item, 'restaurant': rest_name, 'cuisine': cuisine})
                        seen_ids.add(item['id'])

    return render_template('recommendations.html', recommendations=recs[:9])

@app.route('/submit-feedback', methods=['POST'])
@login_required('customer')
def submit_feedback():
    order_id = request.form.get('order_id')
    rating = request.form.get('rating')
    comments = request.form.get('comments', '')
    try:
        feedbacks_col.insert_one({
            "order_id": int(order_id),
            "customer_id": session.get('user_id'),
            "rating": int(rating),
            "comments": comments,
            "timestamp": datetime.utcnow()
        })
    except:
        pass
    return jsonify({"message": "Thank you for your feedback! ❤️"})

# ====================== RIDER ROUTES ======================

@app.route('/rider')
@login_required('delivery_partner')
def rider_hub():
    rider = User.query.get(session.get('user_id'))
    active_jobs = ActiveOrder.query.filter(
        ActiveOrder.rider_id == rider.id,
        ActiveOrder.status.in_(['Placed', 'Preparing', 'Dispatched', 'On the Way'])
    ).count()
    return render_template('rider.html', rider=rider, active_jobs=active_jobs)

@app.route('/rider/delivery')
@login_required('delivery_partner')
def rider_delivery():
    rider_id = session.get('user_id')
    orders = ActiveOrder.query.filter(
        ActiveOrder.rider_id == rider_id,
        ActiveOrder.status.in_(['Placed', 'Preparing', 'Dispatched', 'On the Way'])
    ).order_by(ActiveOrder.created_at.desc()).all()
    unassigned = ActiveOrder.query.filter(
        ActiveOrder.rider_id == None,
        ActiveOrder.status == 'Placed'
    ).all()
    return render_template('delivery.html', orders=orders, unassigned=unassigned)

@app.route('/rider/accept/<int:order_id>', methods=['POST'])
@login_required('delivery_partner')
def rider_accept_order(order_id):
    order = ActiveOrder.query.get_or_404(order_id)
    if order.rider_id is not None:
        flash("Order already assigned.", "error")
        return redirect(url_for('rider_delivery'))
    order.rider_id = session.get('user_id')
    db.session.commit()
    flash(f"Order #{order_id} accepted!", "success")
    return redirect(url_for('rider_delivery'))

@app.route('/rider/order/update-status/<int:order_id>', methods=['POST'])
@login_required('delivery_partner')
def rider_update_status(order_id):
    order = ActiveOrder.query.get_or_404(order_id)
    new_status = request.form.get('status')
    if order.rider_id != session.get('user_id'):
        return "Unauthorized", 403
    if new_status in ORDER_STATUSES:
        order.status = new_status
        db.session.commit()
        try:
            orders_log.update_one({"sql_order_id": order.id}, {"$set": {"status": new_status}})
        except:
            pass
        flash(f"Order status updated to {new_status}.", "success")

        status_messages = {
            'Preparing':  f"👨‍🍳 Your order #{order.id} is now being prepared!",
            'Dispatched': f"🚀 Your order #{order.id} has been dispatched!",
            'On the Way': f"🛵 Your order #{order.id} is on the way!",
            'Delivered':  f"🎉 Your order #{order.id} has been delivered. Enjoy!",
            'Cancelled':  f"❌ Your order #{order.id} has been cancelled."
        }
        if new_status in status_messages and order.customer_id:
            send_notification(
                status_messages[new_status],
                category='success' if new_status == 'Delivered' else 'info',
                user_id=order.customer_id
            )
        send_notification(
            f"📍 Order #{order.id} updated to '{new_status}' by rider.",
            category='info', role_target='admin'
        )

    return redirect(url_for('rider_hub'))

@app.route('/rider/toggle-status', methods=['POST'])
@login_required('delivery_partner')
def rider_toggle_status():
    rider = User.query.get(session.get('user_id'))
    rider.is_online = not rider.is_online
    db.session.commit()
    status = "Online" if rider.is_online else "Offline"
    flash(f"Status changed to {status}.", "success")
    return redirect(url_for('rider_hub'))

# ====================== ADMIN ROUTES ======================

@app.route('/admin')
def admin_hub():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    pending_riders = User.query.filter_by(role='delivery_partner', status='Pending').all()
    total_users = User.query.count()
    active_orders = ActiveOrder.query.filter(ActiveOrder.status.notin_(['Delivered', 'Cancelled'])).count()
    return render_template('admin.html', pending_riders=pending_riders, total_users=total_users, active_orders=active_orders)

@app.route('/admin/approve-rider/<int:rider_id>', methods=['POST'])
def approve_rider(rider_id):
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    rider = User.query.get_or_404(rider_id)
    rider.status = 'Active'
    db.session.commit()
    flash(f"Rider {rider.username} approved.", "success")
    send_notification(
        f"🎉 Your delivery partner account has been approved! You can now go online.",
        category='success', user_id=rider.id
    )
    return redirect(url_for('admin_hub'))

@app.route('/admin/toggle-user/<int:user_id>', methods=['POST'])
def toggle_user_status(user_id):
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    user = User.query.get_or_404(user_id)
    user.status = 'Inactive' if user.status == 'Active' else 'Active'
    db.session.commit()
    flash(f"User {user.username} status updated to {user.status}.", "success")
    return redirect(url_for('admin_users'))

@app.route('/admin/users')
def admin_users():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    users = User.query.order_by(User.id.desc()).all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/analytics')
def admin_analytics():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    total_orders = ActiveOrder.query.count()
    return render_template('admin_analytics.html', total_orders=total_orders)

@app.route('/admin/orders')
def admin_orders_log():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    try:
        orders = list(orders_log.find())
    except:
        orders = []
    return render_template('admin_orders.html', orders=orders)

@app.route('/admin/mongodb-analytics')
def admin_mongodb_analytics():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    try:
        all_orders = list(orders_log.find())
    except:
        all_orders = []

    cuisine_count = defaultdict(int)
    hour_count = defaultdict(int)
    for o in all_orders:
        cuisine_count[o.get('cuisine', 'Unknown')] += 1
        ts = o.get('timestamp')
        if isinstance(ts, datetime):
            hour_count[ts.hour] += 1

    cuisine_labels = json.dumps(list(cuisine_count.keys()))
    cuisine_values = json.dumps(list(cuisine_count.values()))
    hour_labels = json.dumps(sorted(hour_count.keys()))
    hour_values = json.dumps([hour_count[h] for h in sorted(hour_count.keys())])

    return render_template('analytics.html',
                           total_orders=len(all_orders),
                           cuisine_labels=cuisine_labels,
                           cuisine_values=cuisine_values,
                           hour_labels=hour_labels,
                           hour_values=hour_values)

@app.route('/admin/restaurants', methods=['GET', 'POST'])
def admin_restaurants():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    if request.method == 'POST':
        category = request.form.get('category')
        name = request.form.get('name')
        hours = request.form.get('hours', 'N/A')
        available = request.form.get('available') == 'on'
        lat = float(request.form.get('lat') or 24.8607)
        lng = float(request.form.get('lng') or 67.0011)
        if category not in VENDORS:
            VENDORS[category] = {}
        VENDORS[category][name] = {'hours': hours, 'available': available, 'lat': lat, 'lng': lng, 'menu': []}
        flash(f"Restaurant '{name}' added.", "success")
        return redirect(url_for('admin_restaurants'))
    return render_template('admin_restaurants.html', vendors=VENDORS)

@app.route('/admin/restaurants/update', methods=['POST'])
def update_restaurant():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    category = request.form.get('category')
    name = request.form.get('name')
    hours = request.form.get('hours', 'N/A')
    available = request.form.get('available') == 'on'
    if category in VENDORS and name in VENDORS[category]:
        VENDORS[category][name]['hours'] = hours
        VENDORS[category][name]['available'] = available
        flash(f"Restaurant '{name}' updated.", "success")
    return redirect(url_for('admin_restaurants'))

@app.route('/admin/restaurants/delete', methods=['POST'])
def delete_restaurant():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    category = request.form.get('category')
    name = request.form.get('name')
    if category in VENDORS and name in VENDORS[category]:
        del VENDORS[category][name]
        flash(f"Restaurant '{name}' deleted.", "success")
    return redirect(url_for('admin_restaurants'))

@app.route('/admin/import-orders', methods=['POST'])
def import_cleaned_orders():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    flash("CSV import requires a file upload — implement file upload to use this feature.", "error")
    return redirect(url_for('admin_mongodb_analytics'))

# ====================== LOGOUT ======================

@app.route('/auth/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/notifications/feed')
def notifications_feed():
    if not session.get('username'):
        return jsonify([])
    role = session.get('role')
    user_id = session.get('user_id')
    query = Notification.query.filter_by(is_read=False)
    if role == 'admin':
        query = query.filter(
            (Notification.role_target == 'admin') | (Notification.user_id == None)
        )
    elif user_id:
        query = query.filter(
            (Notification.user_id == user_id) | (Notification.role_target == role)
        )
    notifs = query.order_by(Notification.created_at.desc()).limit(15).all()
    return jsonify([{
        'id': n.id,
        'message': n.message,
        'category': n.category,
        'created_at': n.created_at.strftime('%b %d, %H:%M')
    } for n in notifs])

@app.route('/notifications/mark-read', methods=['POST'])
def mark_notifications_read():
    if not session.get('username'):
        return jsonify({'status': 'error'})
    role = session.get('role')
    user_id = session.get('user_id')
    if role == 'admin':
        Notification.query.filter_by(is_read=False, role_target='admin').update({'is_read': True})
    elif user_id:
        Notification.query.filter_by(is_read=False, user_id=user_id).update({'is_read': True})
        Notification.query.filter_by(is_read=False, role_target=role).update({'is_read': True})
    db.session.commit()
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)
