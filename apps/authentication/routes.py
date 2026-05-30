from flask import (
    render_template, redirect, request, url_for, flash, session, current_app, jsonify
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from PIL import Image
from functools import wraps
import os
import uuid
import time
import mysql.connector
import pytz
import re
import logging
from typing import Optional, Dict, Any, List

from apps import get_db_connection
from apps.authentication import blueprint
from apps.utils.decorators import login_required

# Configure logging
logger = logging.getLogger(__name__)

# ============================================
# VALIDATION FUNCTIONS
# ============================================

def validate_email(email: str) -> bool:
    """Validate email format"""
    if not email:
        return False
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_regex, email) is not None

def validate_username(username: str) -> bool:
    """Validate username format (alphanumeric and underscores only)"""
    if not username:
        return False
    return bool(re.match(r'^[a-zA-Z0-9_]{3,50}$', username))

def validate_phone_number(phone: str) -> bool:
    """Validate phone number format"""
    if not phone:
        return True  # Phone is optional
    # Allow international format, spaces, dashes
    return bool(re.match(r'^[\+]?[\d\s\-\(\)]{10,}$', phone))

def validate_password_strength(password: str) -> tuple:
    """Check password strength and return (is_valid, message)"""
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r'\d', password):
        return False, "Password must contain at least one number"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain at least one special character"
    return True, "Password is strong"

def sanitize_input(text: str) -> str:
    """Basic input sanitization"""
    if not text:
        return ""
    # Remove any potential HTML/script tags
    return re.sub(r'<[^>]*>', '', text).strip()

# ============================================
# HELPER FUNCTIONS
# ============================================

def get_kampala_time():
    """Get current time in Kampala timezone"""
    kampala = pytz.timezone("Africa/Kampala")
    return datetime.now(kampala)

def allowed_file(filename, check_image_only=True):
    """Check if the uploaded file has a valid extension."""
    if not filename:
        return False
    
    if '.' not in filename:
        return False
    
    ext = filename.rsplit('.', 1)[1].lower()
    allowed_extensions = current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'gif'})
    
    if check_image_only:
        # For profile and signature images, only allow image formats
        image_extensions = {'png', 'jpg', 'jpeg', 'gif'}
        return ext in image_extensions
    else:
        return ext in allowed_extensions

def update_user_logout(user_id, connection):
    """Helper function to update user logout status"""
    try:
        with connection.cursor(dictionary=True) as cursor:
            current_time = get_kampala_time()
            current_time_naive = current_time.replace(tzinfo=None)
            
            cursor.execute("""
                UPDATE user_activity 
                SET logout_time = %s 
                WHERE user_id = %s AND logout_time IS NULL
                ORDER BY login_time DESC
                LIMIT 1
            """, (current_time_naive, user_id))
            
            # Update users table with last_seen and online status
            cursor.execute("""
                UPDATE users 
                SET is_online = 0, last_seen = %s 
                WHERE id = %s
            """, (current_time_naive, user_id))
            
            connection.commit()
            return True
    except Exception as e:
        logger.error(f"Error updating user logout: {e}")
        return False

def handle_profile_image(profile_image, user_id=None, crop_data=None):
    """Handle profile image upload with cropping and validation"""
    if not profile_image or not profile_image.filename or not allowed_file(profile_image.filename, check_image_only=True):
        return None
    
    # Validate file size (5MB limit)
    profile_image.seek(0, os.SEEK_END)
    file_size = profile_image.tell()
    profile_image.seek(0)
    
    max_size = 5 * 1024 * 1024  # 5MB
    if file_size > max_size:
        logger.warning(f"Profile image too large: {file_size} bytes")
        return None
    
    # Create unique filename
    if user_id:
        filename = f"profile_{user_id}_{int(time.time())}.png"
    else:
        filename = f"profile_new_{int(time.time())}.png"
    
    # Get upload folder from config
    upload_folder = current_app.config.get('UPLOAD_FOLDER')
    if not upload_folder:
        logger.error("UPLOAD_FOLDER not configured")
        return None
    
    # Ensure upload folder exists
    os.makedirs(upload_folder, exist_ok=True)
    
    file_path = os.path.join(upload_folder, filename)
    
    try:
        img = Image.open(profile_image)
        
        # Convert to RGB if necessary (for PNG with transparency)
        if img.mode in ('RGBA', 'LA', 'P'):
            # Create white background for transparent images
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Apply crop if coordinates provided
        if crop_data:
            try:
                x = float(crop_data.get('x', 0))
                y = float(crop_data.get('y', 0))
                w = float(crop_data.get('w', 0))
                h = float(crop_data.get('h', 0))
                
                if w > 0 and h > 0 and x >= 0 and y >= 0:
                    # Ensure crop is within image bounds
                    x = min(x, img.width - 1)
                    y = min(y, img.height - 1)
                    w = min(w, img.width - x)
                    h = min(h, img.height - y)
                    
                    if w > 0 and h > 0:
                        img = img.crop((int(x), int(y), int(x + w), int(y + h)))
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid crop data: {e}")
        
        # Resize image to standard size (200x200)
        img.thumbnail((200, 200), Image.Resampling.LANCZOS)
        
        # Save with optimization
        img.save(file_path, "PNG", optimize=True)
        return filename
    except Exception as e:
        logger.error(f"Error processing profile image: {e}")
        return None

def handle_sign_image(sign_image, user_id=None, crop_data=None):
    """Handle signature image upload with cropping"""
    if not sign_image or not sign_image.filename or not allowed_file(sign_image.filename, check_image_only=True):
        return None
    
    # Validate file size
    sign_image.seek(0, os.SEEK_END)
    file_size = sign_image.tell()
    sign_image.seek(0)
    
    max_size = 5 * 1024 * 1024  # 5MB
    if file_size > max_size:
        logger.warning(f"Signature file too large: {file_size} bytes")
        return None
    
    # Create unique filename
    timestamp = int(time.time())
    if user_id:
        filename = f"signature_{user_id}_{timestamp}.png"
    else:
        filename = f"signature_new_{timestamp}.png"
    
    # Get upload folder from config
    upload_folder = current_app.config.get('UPLOAD_FOLDER')
    if not upload_folder:
        logger.error("UPLOAD_FOLDER not configured")
        return None
    
    # Ensure upload folder exists
    os.makedirs(upload_folder, exist_ok=True)
    
    file_path = os.path.join(upload_folder, filename)
    
    try:
        img = Image.open(sign_image)
        
        # Convert to RGB if necessary
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Apply crop if coordinates provided
        if crop_data:
            try:
                x = float(crop_data.get('x', 0))
                y = float(crop_data.get('y', 0))
                w = float(crop_data.get('w', 0))
                h = float(crop_data.get('h', 0))
                
                if w > 0 and h > 0 and x >= 0 and y >= 0:
                    x = min(x, img.width - 1)
                    y = min(y, img.height - 1)
                    w = min(w, img.width - x)
                    h = min(h, img.height - y)
                    
                    if w > 0 and h > 0:
                        img = img.crop((int(x), int(y), int(x + w), int(y + h)))
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid crop data for signature: {e}")
        
        # Resize signature to reasonable size
        img.thumbnail((300, 150), Image.Resampling.LANCZOS)
        
        img.save(file_path, "PNG", optimize=True)
        return filename
    except Exception as e:
        logger.error(f"Error processing signature image: {e}")
        return None

def get_user_by_id(user_id, connection):
    """Fetch user by ID"""
    try:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            return cursor.fetchone()
    except Exception as e:
        logger.error(f"Error fetching user by ID {user_id}: {e}")
        return None

def user_has_role(required_roles):
    """Decorator for role-based access control"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_role = session.get('role')
            if user_role not in required_roles:
                flash('Access Denied: Insufficient Permissions.', 'warning')
                return redirect(url_for('home_blueprint.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def log_user_activity(user_id: int, action: str, details: str = None):
    """Log user activity for audit trail"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                current_time = get_kampala_time().replace(tzinfo=None)
                # Check if activity_logs table exists, create if not
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS activity_logs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id INT,
                        action VARCHAR(50),
                        details TEXT,
                        timestamp DATETIME,
                        INDEX idx_user_time (user_id, timestamp),
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                    )
                """)
                cursor.execute("""
                    INSERT INTO activity_logs (user_id, action, details, timestamp)
                    VALUES (%s, %s, %s, %s)
                """, (user_id, action, details, current_time))
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to log user activity: {e}")

# ============================================
# ROUTES
# ============================================

@blueprint.route('/', methods=['GET', 'POST'])
def route_default():
    return redirect(url_for('authentication_blueprint.login'))

@blueprint.route('/login', methods=['GET', 'POST'])
def login():
    # Redirect if already logged in
    if session.get('loggedin'):
        return redirect(url_for('home_blueprint.index'))
    
    if request.method == 'POST':
        username = sanitize_input(request.form.get('username', ''))
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Please enter both username and password.', 'danger')
            return render_template('accounts/login.html')
        
        try:
            with get_db_connection() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    # Check if users table exists
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            username VARCHAR(50) UNIQUE NOT NULL,
                            password VARCHAR(255) NOT NULL,
                            role VARCHAR(50) DEFAULT 'applicant',
                            role1 VARCHAR(50),
                            first_name VARCHAR(100),
                            last_name VARCHAR(100),
                            other_name VARCHAR(100),
                            name_sf VARCHAR(100),
                            email VARCHAR(100),
                            phone_number VARCHAR(20),
                            profile_image VARCHAR(255),
                            sign_image VARCHAR(255),
                            is_online BOOLEAN DEFAULT 0,
                            session_token VARCHAR(255),
                            assigned_db VARCHAR(50),
                            failed_attempts INT DEFAULT 0,
                            last_failed_attempt DATETIME,
                            account_locked BOOLEAN DEFAULT FALSE,
                            last_login DATETIME,
                            last_seen DATETIME,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    
                    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
                    user = cursor.fetchone()
                    
                    if not user:
                        # Add small delay to prevent timing attacks
                        time.sleep(0.5)
                        flash('Invalid username or password.', 'danger')
                        return render_template('accounts/login.html')
                    
                    # Check if account is locked
                    if user.get('account_locked'):
                        flash('Account is locked. Please contact administrator.', 'danger')
                        return render_template('accounts/login.html')
                    
                    # Check failed login attempts
                    if user.get('failed_attempts', 0) >= 5:
                        flash('Too many failed attempts. Account locked.', 'danger')
                        return render_template('accounts/login.html')
                    
                    # Verify password with hash
                    if not check_password_hash(user['password'], password):
                        # Increment failed attempts
                        cursor.execute("""
                            UPDATE users 
                            SET failed_attempts = COALESCE(failed_attempts, 0) + 1,
                                last_failed_attempt = %s
                            WHERE id = %s
                        """, (get_kampala_time().replace(tzinfo=None), user['id']))
                        conn.commit()
                        
                        time.sleep(0.5)  # Prevent timing attacks
                        flash('Invalid username or password.', 'danger')
                        return render_template('accounts/login.html')
                    
                    # Reset failed attempts on successful login
                    cursor.execute("""
                        UPDATE users 
                        SET failed_attempts = 0, last_failed_attempt = NULL
                        WHERE id = %s
                    """, (user['id'],))
                    
                    # Create user_activity table if not exists
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS user_activity (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            user_id INT,
                            login_time DATETIME,
                            logout_time DATETIME,
                            ip_address VARCHAR(45),
                            user_agent TEXT,
                            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                            INDEX idx_user_login (user_id, login_time)
                        )
                    """)
                    
                    # Record login
                    login_time = get_kampala_time()
                    login_time_naive = login_time.replace(tzinfo=None)
                    
                    cursor.execute("""
                        INSERT INTO user_activity (user_id, login_time, ip_address, user_agent)
                        VALUES (%s, %s, %s, %s)
                    """, (user['id'], login_time_naive, 
                          request.remote_addr, request.user_agent.string[:500] if request.user_agent else None))
                    
                    cursor.execute("""
                        UPDATE users 
                        SET is_online = 1, last_login = %s, last_seen = %s 
                        WHERE id = %s
                    """, (login_time_naive, login_time_naive, user['id']))
                    
                    # Generate secure session token
                    session_token = str(uuid.uuid4())
                    session['token'] = session_token
                    cursor.execute("UPDATE users SET session_token = %s WHERE id = %s",
                                  (session_token, user['id']))
                    
                    conn.commit()
                    
                    # Set session data
                    session.update({
                        'loggedin': True,
                        'id': user['id'],
                        'username': user['username'],
                        'assigned_db': user.get('assigned_db'),
                        'profile_image': user.get('profile_image'),
                        'first_name': user.get('first_name'),
                        'last_name': user.get('last_name'),
                        'role': user.get('role'),
                        'role1': user.get('role1'),
                        'last_activity': login_time.isoformat(),
                        'login_time': login_time.isoformat()
                    })
                    
                    session.permanent = False
                    
                    # Log successful login
                    log_user_activity(user['id'], 'LOGIN_SUCCESS', f"Logged in from {request.remote_addr}")
                    
                    flash(f"Login successful! Welcome back, {user.get('first_name', username)}!", 'success')
                    return redirect(url_for('home_blueprint.index'))
                    
        except Exception as e:
            logger.error(f"Login error: {e}")
            flash("An error occurred during login. Please try again.", 'danger')
    
    return render_template('accounts/login.html')

@blueprint.before_app_request
def check_token_validity():
    """Check if session token is still valid"""
    if 'loggedin' in session and session.get('loggedin'):
        user_id = session.get('id')
        token = session.get('token')
        
        if not user_id or not token:
            session.clear()
            return redirect(url_for('authentication_blueprint.login'))
        
        try:
            with get_db_connection() as connection:
                with connection.cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT session_token FROM users WHERE id = %s", (user_id,))
                    result = cursor.fetchone()
                    
                    if not result or token != result['session_token']:
                        session.clear()
                        flash('Your session has been invalidated. Please login again.', 'info')
                        return redirect(url_for('authentication_blueprint.login'))
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            session.clear()
            return redirect(url_for('authentication_blueprint.login'))

@blueprint.before_app_request
def check_inactivity():
    """Check for session timeout due to inactivity"""
    if 'loggedin' in session and session.get('loggedin'):
        last_activity_str = session.get('last_activity')
        if last_activity_str:
            try:
                last_activity = datetime.fromisoformat(last_activity_str)
                current_time = get_kampala_time()
                
                # Timeout after 30 minutes
                timeout_minutes = 30
                if (current_time - last_activity) > timedelta(minutes=timeout_minutes):
                    try:
                        with get_db_connection() as connection:
                            update_user_logout(session['id'], connection)
                        session.clear()
                        flash('Session expired due to inactivity.', 'warning')
                        return redirect(url_for('authentication_blueprint.login'))
                    except Exception as e:
                        logger.error(f"Inactivity logout error: {e}")
                        session.clear()
                        return redirect(url_for('authentication_blueprint.login'))
            except Exception as e:
                logger.error(f"Inactivity check error: {e}")
                session['last_activity'] = get_kampala_time().isoformat()
        
        # Update last activity
        session['last_activity'] = get_kampala_time().isoformat()

@blueprint.route('/logout')
def logout():
    """User logout"""
    user_id = session.get('id')
    username = session.get('username')
    
    if user_id:
        try:
            with get_db_connection() as connection:
                update_user_logout(user_id, connection)
                log_user_activity(user_id, 'LOGOUT', 'User logged out')
                logger.info(f"User '{username}' logged out successfully.")
        except Exception as e:
            logger.error(f"Logout error: {e}")
            flash("An error occurred during logout.", 'danger')
    
    session.clear()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('authentication_blueprint.login'))

@blueprint.route('/signup', methods=['GET', 'POST'])
def signup():
    """User registration"""
    if session.get('loggedin'):
        return redirect(url_for('home_blueprint.index'))
    
    if request.method == 'POST':
        # Collect and sanitize form data
        username = sanitize_input(request.form.get('username', ''))
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        first_name = sanitize_input(request.form.get('first_name', ''))
        last_name = sanitize_input(request.form.get('last_name', ''))
        email = sanitize_input(request.form.get('email', ''))
        phone_number = sanitize_input(request.form.get('phone_number', ''))
        
        # Validation
        errors = []
        
        if not all([username, password, first_name, last_name, email]):
            errors.append('Please fill in all required fields.')
        
        if not validate_username(username):
            errors.append('Username must be 3-50 characters (letters, numbers, underscores only).')
        
        if password != confirm_password:
            errors.append('Passwords do not match.')
        
        is_valid, password_msg = validate_password_strength(password)
        if not is_valid:
            errors.append(password_msg)
        
        if not validate_email(email):
            errors.append('Please enter a valid email address.')
        
        if phone_number and not validate_phone_number(phone_number):
            errors.append('Please enter a valid phone number.')
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('accounts/signup.html')
        
        try:
            with get_db_connection() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    # Create users table if not exists
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            username VARCHAR(50) UNIQUE NOT NULL,
                            password VARCHAR(255) NOT NULL,
                            role VARCHAR(50) DEFAULT 'applicant',
                            role1 VARCHAR(50),
                            first_name VARCHAR(100),
                            last_name VARCHAR(100),
                            other_name VARCHAR(100),
                            name_sf VARCHAR(100),
                            email VARCHAR(100),
                            phone_number VARCHAR(20),
                            profile_image VARCHAR(255),
                            sign_image VARCHAR(255),
                            is_online BOOLEAN DEFAULT 0,
                            session_token VARCHAR(255),
                            assigned_db VARCHAR(50),
                            failed_attempts INT DEFAULT 0,
                            last_failed_attempt DATETIME,
                            account_locked BOOLEAN DEFAULT FALSE,
                            last_login DATETIME,
                            last_seen DATETIME,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    
                    # Check existing username
                    cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
                    if cursor.fetchone():
                        flash('Username already exists. Please choose another.', 'danger')
                        return render_template('accounts/signup.html')
                    
                    # Check existing email
                    cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
                    if cursor.fetchone():
                        flash('Email address is already registered.', 'danger')
                        return render_template('accounts/signup.html')
                    
                    # Hash password
                    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    
                    # Insert new user
                    cursor.execute("""
                        INSERT INTO users (
                            username, password, role, first_name, last_name,
                            email, phone_number, is_online, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)
                    """, (username, hashed_password, 'applicant', first_name, 
                          last_name, email, phone_number, 
                          get_kampala_time().replace(tzinfo=None)))
                    
                    conn.commit()
                    flash('Account created successfully! Please sign in.', 'success')
                    return redirect(url_for('authentication_blueprint.login'))
                    
        except Exception as e:
            logger.error(f"Signup error: {e}")
            flash('An error occurred during registration. Please try again.', 'danger')
    
    return render_template('accounts/signup.html')

@login_required
@blueprint.route('/force_logout/<int:user_id>')
@user_has_role(['admin', 'super_admin'])
def force_logout(user_id):
    """Force logout a specific user"""
    # Prevent self force-logout
    if user_id == session.get('id'):
        flash('You cannot force logout yourself.', 'warning')
        return redirect(url_for('authentication_blueprint.manage_users'))
    
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                current_time = get_kampala_time().replace(tzinfo=None)
                
                # Get user info for logging
                cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
                user = cursor.fetchone()
                
                # Update user activity
                cursor.execute("""
                    UPDATE user_activity
                    SET logout_time = %s
                    WHERE user_id = %s AND logout_time IS NULL
                    ORDER BY login_time DESC
                    LIMIT 1
                """, (current_time, user_id))
                
                cursor.execute("UPDATE users SET is_online = 0 WHERE id = %s", (user_id,))
                
                # Invalidate session by generating new token
                new_token = str(uuid.uuid4())
                cursor.execute("UPDATE users SET session_token = %s WHERE id = %s", (new_token, user_id))
                
                connection.commit()
                
                # Log the action
                log_user_activity(session['id'], 'FORCE_LOGOUT', 
                                f"Forced logout of user {user['username'] if user else user_id}")
                
                flash(f"User {user['username'] if user else user_id} has been signed out successfully.", "success")
    except Exception as e:
        logger.error(f"Force logout error: {e}")
        flash(f"Error during forced logout: {str(e)}", "danger")
    
    return redirect(url_for('authentication_blueprint.manage_users'))

@login_required
@blueprint.route('/manage_users')
@user_has_role(['super_admin', 'admin', 'inventory_manager'])
def manage_users():
    """Manage users page with RBAC filtering"""
    current_user_role = session.get('role')
    
    # RBAC hierarchy
    excluded_roles_map = {
        'super_admin': ['super_admin'],
        'admin': ['admin', 'super_admin'],
        'inventory_manager': ['admin', 'inventory_manager', 'super_admin', 'class_teacher']
    }
    
    excluded_roles = excluded_roles_map.get(current_user_role, ['admin', 'super_admin'])
    
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                placeholders = ','.join(['%s'] * len(excluded_roles))
                query = f"""
                    SELECT 
                        u.id, u.username, u.role, u.name_sf, u.is_online, 
                        u.profile_image, u.sign_image, u.email, u.created_at,
                        CONCAT_WS(' ', u.last_name, u.first_name, u.other_name) AS full_name,
                        (SELECT MAX(login_time) FROM user_activity WHERE user_id = u.id) AS last_activity,
                        (SELECT COUNT(*) FROM user_activity WHERE user_id = u.id) AS login_count
                    FROM users u
                    WHERE u.role NOT IN ({placeholders})
                    ORDER BY last_activity DESC, u.username ASC
                """
                cursor.execute(query, tuple(excluded_roles))
                users = cursor.fetchall()
    except Exception as e:
        logger.error(f"Manage users error: {e}")
        flash("System error while retrieving user directory.", "danger")
        return redirect(url_for('home_blueprint.index'))
    
    return render_template('accounts/manage_users.html', users=users, num=len(users))

@login_required
@blueprint.route('/get_all_user_statuses', methods=['GET'])
def get_all_user_statuses():
    """Get online status for all users"""
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                query = """
                    SELECT u.id, u.is_online, u.last_seen,
                           (SELECT MAX(timestamp) FROM activity_logs WHERE user_id = u.id) as last_activity
                    FROM users u
                """
                cursor.execute(query)
                results = cursor.fetchall()
                
                for row in results:
                    if row.get('last_seen'):
                        row['last_seen'] = row['last_seen'].strftime('%Y-%m-%d %H:%M')
                    else:
                        row['last_seen'] = "Never"
                    
                    if row.get('last_activity'):
                        row['last_activity'] = row['last_activity'].strftime('%Y-%m-%d %H:%M')
                
                return jsonify(results)
    except Exception as e:
        logger.error(f"Error in bulk status check: {e}")
        return jsonify({"error": "Failed to fetch user statuses"}), 500

@login_required
@blueprint.route('/activity_logs/<int:id>', methods=['GET', 'POST'])
@user_has_role(['super_admin', 'admin'])
def activity_logs(id):
    """View user activity logs"""
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                query = """
                    SELECT ua.login_time, ua.logout_time, ua.ip_address, ua.user_agent,
                           u.username, u.first_name, u.last_name,
                           TIMEDIFF(ua.logout_time, ua.login_time) as session_duration
                    FROM user_activity ua
                    JOIN users u ON ua.user_id = u.id
                    WHERE ua.user_id = %s
                    ORDER BY ua.login_time DESC
                    LIMIT 100
                """
                cursor.execute(query, (id,))
                activities = cursor.fetchall()
                return render_template('accounts/activity_logs.html', activities=activities)
    except Exception as e:
        logger.error(f"Activity logs error: {e}")
        flash(f"An error occurred: {str(e)}", 'danger')
        return redirect(url_for('authentication_blueprint.manage_users'))

@login_required
@blueprint.route('/add_user', methods=['GET', 'POST'])
@user_has_role(['super_admin', 'admin'])
def add_user():
    """Add new user"""
    if request.method == 'POST':
        # Collect and sanitize form data
        username = sanitize_input(request.form.get('username', ''))
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        role = request.form.get('role', 'applicant')
        first_name = sanitize_input(request.form.get('first_name', ''))
        last_name = sanitize_input(request.form.get('last_name', ''))
        other_name = sanitize_input(request.form.get('other_name', ''))
        name_sf = sanitize_input(request.form.get('name_sf', ''))
        email = sanitize_input(request.form.get('email', ''))
        phone_number = sanitize_input(request.form.get('phone_number', ''))
        
        # Validation
        errors = []
        
        if not all([username, password, first_name, last_name, email]):
            errors.append('Please fill in all required fields.')
        
        if not validate_username(username):
            errors.append('Username must be 3-50 characters (letters, numbers, underscores only).')
        
        if password != confirm_password:
            errors.append('Passwords do not match.')
        
        is_valid, password_msg = validate_password_strength(password)
        if not is_valid:
            errors.append(password_msg)
        
        if not validate_email(email):
            errors.append('Please enter a valid email address.')
        
        if phone_number and not validate_phone_number(phone_number):
            errors.append('Please enter a valid phone number.')
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('accounts/add_user.html', role=session.get('role'))
        
        # Handle file uploads
        profile_image = request.files.get('profile_image')
        sign_image = request.files.get('sign_image')
        
        # Get crop data
        profile_crop = None
        if profile_image and profile_image.filename:
            profile_crop = {
                'x': request.form.get('crop_x'),
                'y': request.form.get('crop_y'),
                'w': request.form.get('crop_w'),
                'h': request.form.get('crop_h')
            }
        
        sign_crop = None
        if sign_image and sign_image.filename:
            sign_crop = {
                'x': request.form.get('sign_x'),
                'y': request.form.get('sign_y'),
                'w': request.form.get('sign_w'),
                'h': request.form.get('sign_h')
            }
        
        try:
            with get_db_connection() as connection:
                with connection.cursor(dictionary=True) as cursor:
                    # Check existing username
                    cursor.execute('SELECT 1 FROM users WHERE username = %s', (username,))
                    if cursor.fetchone():
                        flash('Username already exists.', 'danger')
                        return render_template('accounts/add_user.html', role=session.get('role'))
                    
                    # Check existing email
                    cursor.execute('SELECT 1 FROM users WHERE email = %s', (email,))
                    if cursor.fetchone():
                        flash('Email already exists.', 'danger')
                        return render_template('accounts/add_user.html', role=session.get('role'))
                    
                    # Hash password
                    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    
                    # Process images
                    profile_image_filename = handle_profile_image(profile_image, None, profile_crop) if profile_image and profile_image.filename else None
                    sign_image_filename = handle_sign_image(sign_image, None, sign_crop) if sign_image and sign_image.filename else None
                    
                    # Insert user
                    cursor.execute("""
                        INSERT INTO users 
                        (username, password, role, first_name, last_name, other_name, 
                         profile_image, name_sf, sign_image, email, phone_number, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (username, hashed_password, role, first_name, last_name, other_name, 
                          profile_image_filename, name_sf, sign_image_filename, email, phone_number,
                          get_kampala_time().replace(tzinfo=None)))
                    
                    connection.commit()
                    
                    # Log the action
                    log_user_activity(session['id'], 'ADD_USER', f"Added user {username} with role {role}")
                    
                    flash('User added successfully!', 'success')
                    return redirect(url_for('authentication_blueprint.manage_users'))
                    
        except Exception as err:
            logger.error(f"Add user error: {err}")
            flash(f'Error: {str(err)}', 'danger')
            return render_template('accounts/add_user.html', role=session.get('role'))
    
    return render_template("accounts/add_user.html", role=session.get('role'))

@login_required
@blueprint.route('/edit_user/<int:id>', methods=['GET', 'POST'])
@user_has_role(['super_admin', 'admin'])
def edit_user(id):
    """Edit user information"""
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                user = get_user_by_id(id, connection)
                
                if not user:
                    flash("User not found.", "danger")
                    return redirect(url_for("authentication_blueprint.manage_users"))
                
                if request.method == 'POST':
                    # Collect form data
                    username = sanitize_input(request.form.get('username', ''))
                    first_name = sanitize_input(request.form.get('first_name', ''))
                    last_name = sanitize_input(request.form.get('last_name', ''))
                    other_name = sanitize_input(request.form.get('other_name', ''))
                    name_sf = sanitize_input(request.form.get('name_sf', ''))
                    email = sanitize_input(request.form.get('email', ''))
                    phone_number = sanitize_input(request.form.get('phone_number', ''))
                    password = request.form.get('password', '')
                    role = request.form.get('role', user['role'])
                    role1 = request.form.get('role1', user.get('role1'))
                    
                    # Validate username
                    if not validate_username(username):
                        flash('Username must be 3-50 characters (letters, numbers, underscores only).', 'danger')
                        return render_template("accounts/edit_user.html", user=user)
                    
                    # Check if username already taken by another user
                    cursor.execute("SELECT id FROM users WHERE username = %s AND id != %s", (username, id))
                    if cursor.fetchone():
                        flash('Username already taken by another user.', 'danger')
                        return render_template("accounts/edit_user.html", user=user)
                    
                    # Validate email
                    if email and not validate_email(email):
                        flash('Please enter a valid email address.', 'danger')
                        return render_template("accounts/edit_user.html", user=user)
                    
                    # Normalize role1
                    if role1 in ('None', '', None):
                        role1 = None
                    
                    # Handle password update
                    if password:
                        is_valid, password_msg = validate_password_strength(password)
                        if not is_valid:
                            flash(password_msg, 'danger')
                            return render_template("accounts/edit_user.html", user=user)
                        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    else:
                        hashed_password = user['password']
                    
                    # Handle image uploads
                    new_profile_file = request.files.get('profile_image')
                    new_sign_file = request.files.get('sign_image')
                    
                    # Get crop data
                    profile_crop = None
                    if new_profile_file and new_profile_file.filename:
                        profile_crop = {
                            'x': request.form.get('crop_x'),
                            'y': request.form.get('crop_y'),
                            'w': request.form.get('crop_w'),
                            'h': request.form.get('crop_h')
                        }
                    
                    sign_crop = None
                    if new_sign_file and new_sign_file.filename:
                        sign_crop = {
                            'x': request.form.get('sign_x'),
                            'y': request.form.get('sign_y'),
                            'w': request.form.get('sign_w'),
                            'h': request.form.get('sign_h')
                        }
                    
                    # Process images
                    profile_image_path = handle_profile_image(new_profile_file, id, profile_crop) if new_profile_file and new_profile_file.filename else user['profile_image']
                    sign_image_path = handle_sign_image(new_sign_file, id, sign_crop) if new_sign_file and new_sign_file.filename else user['sign_image']
                    
                    # Delete old images if replaced
                    if profile_image_path != user['profile_image'] and user['profile_image']:
                        old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], user['profile_image'])
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    
                    if sign_image_path != user['sign_image'] and user['sign_image']:
                        old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], user['sign_image'])
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    
                    # Update database
                    cursor.execute("""
                        UPDATE users 
                        SET username = %s, first_name = %s, last_name = %s, other_name = %s,
                            name_sf = %s, password = %s, role = %s, role1 = %s,
                            profile_image = %s, sign_image = %s, email = %s, phone_number = %s
                        WHERE id = %s
                    """, (username, first_name, last_name, other_name, name_sf,
                          hashed_password, role, role1, profile_image_path, sign_image_path,
                          email, phone_number, id))
                    
                    connection.commit()
                    
                    # Log the action
                    log_user_activity(session['id'], 'EDIT_USER', f"Edited user {username}")
                    
                    flash("User updated successfully!", "success")
                    
                    # Update session if current user
                    if session.get('id') == id:
                        session['first_name'] = first_name
                        session['last_name'] = last_name
                        session['role'] = role
                        session['profile_image'] = profile_image_path
                        session['username'] = username
                    
                    return redirect(url_for("authentication_blueprint.manage_users"))
                
                return render_template("accounts/edit_user.html", user=user)
                
    except Exception as e:
        logger.error(f"Edit user error: {e}")
        flash(f"Error updating user: {str(e)}", "danger")
        return redirect(url_for("authentication_blueprint.manage_users"))

@login_required
@blueprint.route('/view_user/<int:id>', methods=['GET'])
@user_has_role(['super_admin', 'admin', 'inventory_manager'])
def view_user(id):
    """View user details"""
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                user = get_user_by_id(id, connection)
                
                if not user:
                    flash("User not found.", "danger")
                    return redirect(url_for("authentication_blueprint.manage_users"))
                
                # Get sub-categories
                cursor.execute("""
                    SELECT sub.sub_category_id, sub.name AS sub_category_name, 
                           sub.description AS sub_category_description, cat.name AS category_name
                    FROM sub_category sub
                    JOIN category_list cat ON sub.category_id = cat.CategoryID
                    ORDER BY cat.name, sub.name
                """)
                all_sub_categories = cursor.fetchall()
                
                # Get user's assigned sub-categories
                cursor.execute('SELECT sub_category_id FROM other_roles WHERE user_id = %s', (id,))
                user_sub_category_ids = {row['sub_category_id'] for row in cursor.fetchall()}
    except Exception as e:
        logger.error(f"View user error: {e}")
        flash("Error loading user details.", "danger")
        return redirect(url_for("authentication_blueprint.manage_users"))
    
    return render_template("accounts/view_user.html", user=user, 
                          all_sub_categories=all_sub_categories,
                          user_sub_category_ids=user_sub_category_ids)

@login_required
@blueprint.route('/edit_user_roles/<int:id>', methods=['GET', 'POST'])
@user_has_role(['super_admin', 'admin'])
def edit_user_roles(id):
    """Edit user sub-category roles"""
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                user = get_user_by_id(id, connection)
                if not user:
                    flash("User not found.", "danger")
                    return redirect(url_for('authentication_blueprint.manage_users'))
                
                if request.method == 'POST':
                    selected_sub_categories = request.form.getlist('sub_categories')
                    
                    # Clear existing roles
                    cursor.execute('DELETE FROM other_roles WHERE user_id = %s', (id,))
                    
                    # Insert new roles
                    for sub_category_id in selected_sub_categories:
                        cursor.execute("""
                            INSERT INTO other_roles (user_id, sub_category_id) 
                            VALUES (%s, %s)
                        """, (id, sub_category_id))
                    
                    connection.commit()
                    
                    # Log the action
                    log_user_activity(session['id'], 'EDIT_USER_ROLES', 
                                    f"Updated sub-category roles for user {user['username']}")
                    
                    flash('User roles updated successfully!', 'success')
                    return redirect(url_for('authentication_blueprint.manage_users'))
                
                # GET request
                cursor.execute('SELECT * FROM sub_category ORDER BY name')
                all_sub_categories = cursor.fetchall()
                
                cursor.execute('SELECT sub_category_id FROM other_roles WHERE user_id = %s', (id,))
                user_sub_category_ids = {row['sub_category_id'] for row in cursor.fetchall()}
    except Exception as e:
        logger.error(f"Edit user roles error: {e}")
        flash("Error updating user roles.", "danger")
        return redirect(url_for('authentication_blueprint.manage_users'))
    
    return render_template("accounts/edit_user_roles.html", user=user,
                          all_sub_categories=all_sub_categories,
                          user_sub_category_ids=user_sub_category_ids)

@login_required
@blueprint.route('/view_user_cat_roles/<int:id>', methods=['GET'])
@user_has_role(['super_admin', 'admin'])
def view_user_cat_roles(id):
    """View user category roles"""
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                user = get_user_by_id(id, connection)
                
                if not user:
                    flash("User not found.", "danger")
                    return redirect(url_for("authentication_blueprint.manage_users"))
                
                cursor.execute('SELECT CategoryID, name, description FROM category_list ORDER BY name')
                all_categories = cursor.fetchall()
                
                cursor.execute('SELECT category_id FROM category_roles WHERE user_id = %s', (id,))
                user_category_ids = {row['category_id'] for row in cursor.fetchall()}
    except Exception as e:
        logger.error(f"View user cat roles error: {e}")
        flash("Error loading user roles.", "danger")
        return redirect(url_for("authentication_blueprint.manage_users"))
    
    return render_template("accounts/view_user_cat_roles.html", user=user,
                          all_categories=all_categories,
                          user_category_ids=user_category_ids)

@login_required
@blueprint.route('/edit_user_cat_roles/<int:id>', methods=['GET', 'POST'])
@user_has_role(['super_admin', 'admin'])
def edit_user_cat_roles(id):
    """Edit user category roles"""
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                user = get_user_by_id(id, connection)
                if not user:
                    flash("User not found.", "danger")
                    return redirect(url_for('authentication_blueprint.manage_users'))
                
                if request.method == 'POST':
                    selected_categories = request.form.getlist('categories')
                    
                    # Clear existing roles
                    cursor.execute('DELETE FROM category_roles WHERE user_id = %s', (id,))
                    
                    # Insert new roles
                    for category_id in selected_categories:
                        cursor.execute("""
                            INSERT INTO category_roles (user_id, category_id) 
                            VALUES (%s, %s)
                        """, (id, category_id))
                    
                    connection.commit()
                    
                    # Log the action
                    log_user_activity(session['id'], 'EDIT_USER_CAT_ROLES', 
                                    f"Updated category roles for user {user['username']}")
                    
                    flash('User category roles updated successfully!', 'success')
                    return redirect(url_for('authentication_blueprint.manage_users'))
                
                # GET request
                cursor.execute('SELECT * FROM category_list ORDER BY name')
                all_categories = cursor.fetchall()
                
                cursor.execute('SELECT category_id FROM category_roles WHERE user_id = %s', (id,))
                user_category_ids = {row['category_id'] for row in cursor.fetchall()}
    except Exception as e:
        logger.error(f"Edit user cat roles error: {e}")
        flash("Error updating user category roles.", "danger")
        return redirect(url_for('authentication_blueprint.manage_users'))
    
    return render_template("accounts/edit_user_cat_roles.html", user=user,
                          all_categories=all_categories,
                          user_category_ids=user_category_ids)

@login_required
@blueprint.route('/api/user/profile-image')
def profile_image():
    """Get current user's profile image"""
    if 'profile_image' in session and session.get('loggedin'):
        return jsonify({'profile_image': session['profile_image']})
    return jsonify({'error': 'Not logged in'}), 401

@login_required
@blueprint.route('/delete_user/<int:id>', methods=['POST'])
@user_has_role(['super_admin', 'admin'])
def delete_user(id):
    """Delete a user"""
    # Prevent self-deletion
    if id == session.get('id'):
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('authentication_blueprint.manage_users'))
    
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                # Get user info before deletion
                cursor.execute("SELECT username, profile_image, sign_image FROM users WHERE id = %s", (id,))
                user = cursor.fetchone()
                
                if not user:
                    flash('User not found.', 'danger')
                    return redirect(url_for('authentication_blueprint.manage_users'))
                
                # Delete user's images
                upload_folder = current_app.config.get('UPLOAD_FOLDER')
                if upload_folder:
                    if user.get('profile_image'):
                        profile_path = os.path.join(upload_folder, user['profile_image'])
                        if os.path.exists(profile_path):
                            os.remove(profile_path)
                    
                    if user.get('sign_image'):
                        sign_path = os.path.join(upload_folder, user['sign_image'])
                        if os.path.exists(sign_path):
                            os.remove(sign_path)
                
                # Delete user (cascading should handle related tables)
                cursor.execute('DELETE FROM users WHERE id = %s', (id,))
                connection.commit()
                
                # Log the action
                log_user_activity(session['id'], 'DELETE_USER', f"Deleted user {user['username']}")
                
                flash('User deleted successfully!', 'success')
    except mysql.connector.Error as err:
        logger.error(f"Delete user error: {err}")
        flash(f'Error: {str(err)}', 'danger')
    
    return redirect(url_for('authentication_blueprint.manage_users'))

@login_required
@blueprint.route('/edit_user_profile/<int:id>', methods=['GET', 'POST'])
def edit_user_profile(id):
    """Edit user's own profile (limited fields)"""
    # Ensure user can only edit their own profile
    if session.get('id') != id:
        flash('You can only edit your own profile.', 'danger')
        return redirect(url_for('home_blueprint.index'))
    
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                if request.method == 'POST':
                    username = sanitize_input(request.form.get('username', ''))
                    first_name = sanitize_input(request.form.get('first_name', ''))
                    last_name = sanitize_input(request.form.get('last_name', ''))
                    other_name = sanitize_input(request.form.get('other_name', ''))
                    email = sanitize_input(request.form.get('email', ''))
                    phone_number = sanitize_input(request.form.get('phone_number', ''))
                    password = request.form.get('password', '')
                    confirm_password = request.form.get('confirm_password', '')
                    
                    current_user = get_user_by_id(id, connection)
                    if not current_user:
                        flash('User not found.', 'danger')
                        return redirect(url_for('home_blueprint.index'))
                    
                    # Validate username
                    if not validate_username(username):
                        flash('Username must be 3-50 characters (letters, numbers, underscores only).', 'danger')
                        return render_template('accounts/edit_user_profile.html', user=current_user)
                    
                    # Check username uniqueness
                    cursor.execute("SELECT id FROM users WHERE username = %s AND id != %s", (username, id))
                    if cursor.fetchone():
                        flash('Username already taken.', 'danger')
                        return render_template('accounts/edit_user_profile.html', user=current_user)
                    
                    # Validate email
                    if email and not validate_email(email):
                        flash('Please enter a valid email address.', 'danger')
                        return render_template('accounts/edit_user_profile.html', user=current_user)
                    
                    # Handle password update
                    if password:
                        if password != confirm_password:
                            flash('Passwords do not match.', 'danger')
                            return render_template('accounts/edit_user_profile.html', user=current_user)
                        
                        is_valid, password_msg = validate_password_strength(password)
                        if not is_valid:
                            flash(password_msg, 'danger')
                            return render_template('accounts/edit_user_profile.html', user=current_user)
                        
                        final_password = generate_password_hash(password, method='pbkdf2:sha256')
                    else:
                        final_password = current_user['password']
                    
                    # Handle images
                    profile_file = request.files.get('profile_image')
                    sign_file = request.files.get('sign_image')
                    
                    profile_crop = None
                    if profile_file and profile_file.filename:
                        profile_crop = {
                            'x': request.form.get('crop_x'),
                            'y': request.form.get('crop_y'),
                            'w': request.form.get('crop_w'),
                            'h': request.form.get('crop_h')
                        }
                    
                    sign_crop = None
                    if sign_file and sign_file.filename:
                        sign_crop = {
                            'x': request.form.get('sign_x'),
                            'y': request.form.get('sign_y'),
                            'w': request.form.get('sign_w'),
                            'h': request.form.get('sign_h')
                        }
                    
                    profile_path = handle_profile_image(profile_file, id, profile_crop) if profile_file and profile_file.filename else current_user['profile_image']
                    sign_path = handle_sign_image(sign_file, id, sign_crop) if sign_file and sign_file.filename else current_user['sign_image']
                    
                    # Delete old images if replaced
                    if profile_path != current_user['profile_image'] and current_user['profile_image']:
                        old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], current_user['profile_image'])
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    
                    if sign_path != current_user['sign_image'] and current_user['sign_image']:
                        old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], current_user['sign_image'])
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    
                    # Update user (role and role1 are not changed here for security)
                    cursor.execute("""
                        UPDATE users 
                        SET username = %s, first_name = %s, last_name = %s, 
                            other_name = %s, password = %s, profile_image = %s, 
                            sign_image = %s, email = %s, phone_number = %s
                        WHERE id = %s
                    """, (username, first_name, last_name, other_name,
                          final_password, profile_path, sign_path, email, phone_number, id))
                    
                    connection.commit()
                    
                    # Log the action
                    log_user_activity(id, 'PROFILE_UPDATE', 'User updated their own profile')
                    
                    # Update session
                    session['username'] = username
                    session['first_name'] = first_name
                    session['last_name'] = last_name
                    session['profile_image'] = profile_path
                    
                    flash('Profile updated successfully!', 'success')
                    return redirect(url_for('home_blueprint.index'))
                
                # GET request
                user = get_user_by_id(id, connection)
                if not user:
                    flash('User not found!', 'danger')
                    return redirect(url_for('home_blueprint.index'))
                
                return render_template('accounts/edit_user_profile.html', user=user)
                
    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        flash("An unexpected error occurred. Please try again.", "danger")
        return redirect(url_for('home_blueprint.index'))

@blueprint.route('/check_username', methods=['POST'])
@login_required
def check_username():
    """Check if username exists (AJAX endpoint)"""
    username = sanitize_input(request.form.get('username', ''))
    
    if not username:
        return jsonify({"error": "Username required"}), 400
    
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT id FROM users WHERE username = %s",
                    (username,)
                )
                user = cursor.fetchone()
                
                # Exclude current user if editing
                user_id = request.form.get('user_id')
                if user and user_id and str(user['id']) == user_id:
                    return jsonify({"exists": False})
                
                return jsonify({"exists": user is not None})
    
    except Exception as e:
        logger.error(f"Check username error: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================
# ERROR HANDLERS
# ============================================

@blueprint.errorhandler(403)
def access_forbidden(error):
    return render_template('home/page-403.html'), 403

@blueprint.errorhandler(404)
def not_found_error(error):
    return render_template('home/page-404.html'), 404

@blueprint.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return render_template('home/page-500.html'), 500