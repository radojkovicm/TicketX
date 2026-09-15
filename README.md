# TicketX - Ticket Management System

> **TicketX** is a self-hosted helpdesk / ticketing web app for internal IT and cross-department support. Employees open tickets, staff assign, comment on, and resolve them, and everyone stays in sync through email notifications — with role-based access, watchers, attachments, and a full audit trail. Built with Python/Flask and SQLite, deployable on IIS.

## Overview

**TicketX** is a comprehensive, enterprise-grade ticket management system built with Python and Flask. The application is designed to handle ticket creation, assignment, tracking, and resolution across multiple departments and users. The system has been deployed on IIS (Internet Information Services) and includes advanced features such as email notifications, user management, role-based access control, and audit logging.

---

## Getting Started

```bash
# 1. Clone and enter the project
git clone https://github.com/radojkovicm/TicketX.git
cd TicketX

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env          # Windows  (cp on Linux/macOS)
# then edit .env: set FLASK_SECRET_KEY, SMTP_* and INITIAL_ADMIN_* values

# 5. Run
python app.py                   # http://localhost:5000
```

On first run, an initial admin is created from the `INITIAL_ADMIN_*` values in
`.env`. **Log in and change that password immediately**, then set
`DISABLE_INITIAL_ADMIN_CREATION=true`. The SQLite database is created
automatically on startup.

> **Note:** `.env`, `*.db`, and the `uploads/` folders are git-ignored — never
> commit real secrets or production data. Generate a secret key with
> `python -c "import secrets; print(secrets.token_hex(32))"`.

---

## Technologies Used

### Backend Framework & Core
- **Flask** (2.3.3) - Lightweight Python web framework for routing and request handling
- **Flask-WTF** (1.2.1) - CSRF protection for all state-changing requests
- **Werkzeug** (2.3.7) - WSGI utility library for secure password hashing and file uploads
- **Jinja2** (3.1.2) - Templating engine for dynamic HTML rendering
- **MarkupSafe** (3.0.2) - Safe string marking for template rendering

### Database
- **SQLite** (built-in) - Lightweight relational database with:
  - WAL (Write-Ahead Logging) mode for improved concurrency
  - Per-request connections closed automatically via a Flask teardown handler
  - Optimized PRAGMA settings (64MB cache, foreign keys enabled)
  - Automatic schema initialization and migrations

### Data Processing & Analysis
- **Pandas** (2.2.3) - Data manipulation and analysis for bulk imports/exports
- **NumPy** (2.2.2) - Numerical computing for data operations
- **OpenPyXL** (3.1.5) - Excel file reading/writing support
- **Pillow** (12.0.0) - Image processing for attachment handling

### Email & Notifications
- **SMTP Integration** - Configurable email delivery with:
  - SSL/TLS support for secure connections
  - Gmail fallback mechanisms
  - Async email sending to prevent request blocking
  - Beautiful HTML email templates
  
### Google Integration
- **Google API Client** (2.187.0) - Google API integration
- **Google Auth** (2.41.1) - OAuth authentication support
- **Google Auth OAuthlib** (1.2.3) - OAuth protocol support

### Web Server & Deployment
- **wfastcgi** - Fast CGI module for IIS deployment
- Configured for HTTPS with secure session cookies
- Cross-origin request handling

### Environment & Configuration
- **python-dotenv** (1.0.1) - Environment variable management from `.env` files
- Session management with configurable timeout (default: 1 hour)

### Utility Libraries
- **Requests** (2.32.3) - HTTP client for external API calls
- **python-dateutil** (2.9.0) - Date/time utilities
- **PyTZ** (2024.2) - Timezone support
- **Click** (8.2.1) - Command-line utilities
- **Certifi** & **urllib3** - SSL/TLS certificate and HTTP support

---

## System Architecture

### Directory Structure

```
TicketX/
├── app.py                          # Main Flask application and route handlers
├── models.py                       # Database models (User, Ticket, Category)
├── database.py                     # Database connections and schema initialization
├── auth.py                         # Authentication decorators and session management
├── security.py                     # Password hashing/verification helpers
├── notifications.py                # Email notification system
├── mailer.py                       # SMTP email sending logic
├── sql.py                          # SQL utility functions
├── migration.sql                   # Database indices and optimization
├── migration2.py                   # Schema migration utilities
├── db_check.py                     # Database validation and diagnostics
├── delete_db.py                    # Database cleanup utility
├── inspect_ticket.py               # Ticket inspection tools
├── run_notify.py                   # Notification scheduling/execution
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment config template (copy to .env)
├── .gitignore                      # Excludes .env, *.db, uploads, caches
│
├── templates/                      # Jinja2 HTML templates
│   ├── base.html                   # Base template with navigation
│   ├── login.html                  # User login page
│   ├── dashboard.html              # Main dashboard
│   ├── create_ticket.html          # Ticket creation form
│   ├── ticket_detail.html          # Ticket detail view
│   ├── edit_ticket.html            # Ticket editing
│   ├── admin_panel.html            # Admin management interface
│   ├── admin_logs.html             # Activity audit logs
│   ├── my_profile.html             # User profile management
│   ├── edit_user.html              # User editing (admin)
│   ├── edit_category.html          # Category management
│   ├── edit_department.html        # Department management
│   ├── import_users.html           # Bulk user import
│   ├── manage_department_users.html # Department user assignment
│   ├── search_results.html         # Ticket search results
│   └── partials/
│       └── ticket_table.html       # Reusable ticket table component
│
├── static/                         # Static assets
│   ├── css/
│   │   └── style.css               # Main stylesheet
│   ├── js/
│   │   └── main.js                 # Client-side JavaScript
│   └── uploads/                    # Uploaded files (organized by date)
│       └── 2025-11/                # Year-Month directory structure
│
└── logs/                           # Application logs
```

---

## Core Database Schema

### 1. **users** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Unique user identifier |
| username | TEXT UNIQUE | Login username |
| password | TEXT | Password hash (werkzeug PBKDF2; legacy SHA-256 auto-migrated on login) |
| email | TEXT | User email for notifications |
| full_name | TEXT | User's display name |
| role | TEXT | 'admin' or 'user' role |
| department_id | INTEGER FK | Associated department |
| is_department_head | INTEGER | Flag if user is department head |
| created_at | TIMESTAMP | Account creation time |

**Usage**: Authentication, role-based access control (RBAC), notification recipient lookup

---

### 2. **tickets** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Unique ticket identifier |
| title | TEXT | Ticket subject/title |
| description | TEXT | Detailed issue description |
| created_by | INTEGER FK | User who created ticket |
| priority | TEXT | 'low', 'medium', 'high' |
| status | TEXT | 'new', 'assigned', 'in_progress', 'closed', etc. |
| created_at | TEXT | Creation timestamp |
| updated_at | TEXT | Last modification timestamp |
| due_date | TEXT | Ticket deadline |
| category_id | INTEGER FK | Category/type of ticket |
| assigned_to | TEXT | User ID or 'IT' for unassigned |
| is_private | INTEGER | Privacy flag for restricted visibility |

**Usage**: Core ticket tracking, filtering by status/priority, department assignment tracking

**Key Feature**: `assigned_to` supports both numeric user IDs and special 'IT' value for bulk IT assignments

---

### 3. **comments** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Unique comment ID |
| ticket_id | INTEGER FK | Associated ticket |
| user_id | INTEGER FK | Comment author |
| comment | TEXT | Comment content |
| created_at | TIMESTAMP | Comment timestamp |

**Usage**: Ticket communication and discussion tracking

**ON DELETE**: CASCADE (comments deleted when ticket is deleted)

---

### 4. **attachments** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Unique attachment ID |
| ticket_id | INTEGER FK | Associated ticket |
| filename | TEXT | Stored filename (secure hash) |
| original_filename | TEXT | Original uploaded filename |
| file_path | TEXT | Server file path |
| uploaded_by | INTEGER FK | User who uploaded |
| created_at | TIMESTAMP | Upload timestamp |

**Usage**: File attachment storage and retrieval

**Security**: Filenames are hashed; supports: PNG, JPG, GIF, PDF, DOC, DOCX, TXT, XLSX, XLS, ZIP, RAR (max 10MB)

**ON DELETE**: CASCADE (files deleted with ticket)

---

### 5. **ticket_watchers** Table
| Column | Type | Purpose |
|--------|------|---------|
| ticket_id | INTEGER FK | Watched ticket |
| user_id | INTEGER FK | Watching user |
| PRIMARY KEY | (ticket_id, user_id) | Unique constraint |

**Usage**: Users can "watch" tickets for notifications without being assigned

---

### 6. **ticket_activity_log** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Log entry ID |
| ticket_id | INTEGER FK | Audited ticket |
| user_id | INTEGER FK | User performing action |
| action_type | TEXT | 'reassigned', 'status_changed', 'watcher_added', etc. |
| old_value | TEXT | Previous value |
| new_value | TEXT | New value |
| details | TEXT | Additional context (JSON format) |
| created_at | TIMESTAMP | Action timestamp |

**Usage**: Comprehensive audit trail for compliance and tracking

**Supported Actions**:
- Ticket assignment changes
- Status transitions
- Watcher management
- Attachment uploads
- Comment additions

---

### 7. **ticket_muted_users** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Record ID |
| ticket_id | INTEGER FK | Muted ticket |
| user_id | INTEGER FK | User muting notifications |
| muted_at | TIMESTAMP | Mute timestamp |
| UNIQUE(ticket_id, user_id) | Constraint | Prevent duplicate entries |

**Usage**: Allow users to mute notifications for specific tickets

---

### 8. **departments** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Department ID |
| name | TEXT UNIQUE | Department name |
| description | TEXT | Department description |
| created_at | TIMESTAMP | Creation timestamp |

**Usage**: Organizational structure and user grouping

---

### 9. **categories** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Category ID |
| name | TEXT UNIQUE | Category name (e.g., 'Hardware', 'Software', 'Network') |
| description | TEXT | Category details |
| created_at | TIMESTAMP | Creation timestamp |

**Usage**: Ticket classification and filtering

**Default Categories**:
- Hardware
- Software
- Network
- Access
- Other

---

### 10. **ticket_templates** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Template ID |
| user_id | INTEGER FK | Template owner |
| name | TEXT | Template name |
| title | TEXT | Template ticket title |
| description | TEXT | Template description |
| priority | TEXT | Default priority |
| category_id | INTEGER FK | Default category |
| created_at | TIMESTAMP | Creation time |

**Usage**: Reusable ticket templates for quick ticket creation

---

### 11. **activity_logs** Table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PRIMARY KEY | Log ID |
| user_id | INTEGER FK | User performing action |
| action | TEXT | Action description |
| details | TEXT | Additional info |
| ip_address | TEXT | User IP address |
| created_at | TIMESTAMP | Action timestamp |

**Usage**: General system activity audit trail

---

## API Routes & Features

### Authentication Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Landing page redirect to dashboard |
| `/login` | GET/POST | User login page and authentication |
| `/logout` | GET | Clear session and logout |

### User Management Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/my_profile` | GET/POST | User profile and password change |
| `/edit_user/<id>` | GET | Edit user details form |
| `/update_user/<id>` | POST | Submit user updates |
| `/import_users` | GET/POST | Bulk user import via Excel |
| `/add_user_manual` | POST | Create single user manually |
| `/debug_users` | GET | Debug endpoint for user data |

### Ticket Management Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/dashboard` | GET | Main ticket dashboard with filtering |
| `/create_ticket` | GET/POST | Create new ticket form |
| `/ticket/<id>` | GET | View ticket details and comments |
| `/edit_ticket/<id>` | GET/POST | Edit ticket details |
| `/update_ticket_status` | POST | Update ticket status |
| `/reopen_ticket/<id>` | POST | Reopen closed ticket |
| `/mark_resolved/<id>` | POST | Mark ticket as resolved |
| `/assign_ticket` | POST | Assign ticket to user |
| `/reassign_ticket/<id>` | POST | Change ticket assignment |
| `/bulk_assign_it_tickets` | POST | Batch assign multiple tickets |

### Comment & Attachment Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/add_comment` | POST | Add comment to ticket |
| `/upload_attachment` | POST | Upload file to ticket |
| `/delete_attachment/<id>` | POST | Remove attachment |
| `/download_attachment/<id>` | GET | Download file |

### Watcher & Notification Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/add_watchers/<id>` | POST | Add users to watch ticket |
| `/remove_watcher/<id>` | POST | Remove ticket watcher |
| `/ticket/<id>/mute` | POST | Mute notifications |
| `/ticket/<id>/unmute` | POST | Unmute notifications |

### Template Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/save_template` | POST | Save ticket as template |
| `/load_template/<id>` | GET | Load saved template |
| `/delete_template/<id>` | POST | Delete template |

### Category Management Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/add_category` | POST | Create new category |
| `/edit_category/<id>` | GET/POST | Edit category |
| `/delete_category/<id>` | POST | Delete category |

### Department Management Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/add_department` | POST | Create new department |
| `/edit_department/<id>` | GET/POST | Edit department |
| `/delete_department/<id>` | POST | Delete department |
| `/assign_department` | POST | Assign user to department |
| `/manage_department_users/<id>` | GET | Department user management |
| `/add_user_to_department` | POST | Assign user to department |
| `/remove_user_from_department` | POST | Remove user from department |
| `/toggle_department_head` | POST | Make/unmake department head |

### Search & Filtering Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/search` | GET | Full-text ticket search |
| `/api/users` | GET | Get users for dropdowns (JSON) |
| `/get_all_users` | GET | Retrieve all users |

### Admin & Audit Routes
| Route | Method | Purpose |
|-------|--------|---------|
| `/admin` | GET | Admin control panel |
| `/admin/logs` | GET | View activity audit logs |

---

## Key Features & Implementation Details

### 1. Authentication & Authorization
- **Secure Password Hashing**: werkzeug PBKDF2-SHA256 (salted). Legacy unsalted
  SHA-256 hashes are still accepted and automatically re-hashed on next login,
  so existing accounts keep working without a forced reset.
- **CSRF Protection**: Flask-WTF `CSRFProtect` guards every POST/PUT/PATCH/DELETE;
  tokens are injected automatically into all forms and `fetch()` calls.
- **Role-Based Access Control (RBAC)**:
  - `admin` - Full system access
  - `user` - Standard user permissions
  - `department_head` - Department-level administration
- **Session Management**:
  - Configurable timeout (default: 1 hour via `SESSION_TIMEOUT_SECONDS`)
  - Automatic session refresh on activity
  - HTTPS-enforced secure cookies when `IS_HTTPS=true`

### 2. Email Notification System
- **Async Email Sending**: Notifications sent in background threads
- **Multiple SMTP Configurations**:
  - Configurable SMTP server, port, username, password
  - SSL/TLS support
  - Gmail fallback mechanisms
  - Environment-based configuration
  
- **Notification Triggers**:
  - Ticket creation (`notify_on_ticket_created`)
  - Status changes (`notify_on_status_change`)
  - New comments (`notify_on_comment`)
  - Attachment uploads (`notify_on_attachment`)
  - Ticket assignment (`notify_on_assigned`, `notify_on_reassigned`)
  - Ticket closure (`notify_on_closed`)
  - Watcher additions (`notify_on_watchers_added`)

- **Smart Notification Rules**:
  - Exclude action originator option (`EXCLUDE_ACTOR_FROM_NOTIFICATIONS`)
  - IT department special handling (`IT_MAILBOX`)
  - Muted ticket support (users can opt-out)
  - Watcher notifications

### 3. File Upload Security
- **Whitelist-Based Validation**:
  - Extension checking (png, jpg, jpeg, gif, pdf, doc, docx, txt, xlsx, xls, zip, rar)
  - MIME type validation against hardcoded whitelist
  - Max file size: 10MB
  
- **Secure File Handling**:
  - Filenames hashed for storage
  - Original filename preserved for download
  - Organized by date: `static/uploads/YYYY-MM/`
  - Files deleted with ticket (CASCADE)

### 4. Database Optimization
- **Per-request Connections**: One SQLite connection per request, auto-closed on teardown
- **WAL Mode**: Write-Ahead Logging for better concurrent access
- **Query Optimization**:
  - Composite indices on frequently queried columns
  - Status + assigned user filtering
  - Updated timestamp sorting
  - Foreign key constraints enabled
  
- **Automatic Schema Initialization**: Database auto-creates on first run

### 5. Audit & Compliance
- **Comprehensive Activity Logging**:
  - `ticket_activity_log` - Ticket-specific changes
  - `activity_logs` - General system activity with IP tracking
  
- **Tracked Actions**:
  - User assignments/reassignments
  - Status changes with old/new values
  - Comment additions
  - Attachment uploads
  - Watcher modifications

### 6. User Management
- **Bulk Import**: Excel file import with Pandas
- **Department Assignment**: Users grouped by departments
- **Department Head Role**: Special administrative permissions
- **User Search**: Full user database accessible via API

### 7. Ticket Templates
- **Quick Ticket Creation**: Save and reuse ticket templates
- **Template Customization**: Pre-fill title, description, priority, category
- **User-Specific**: Each user maintains personal templates

### 8. Multi-Department Support
- Ticket assignment to departments
- Department-head management of department tickets
- Per-department user management
- Department-based filtering

---

## Environment Configuration

The system uses a `.env` file for configuration. Key variables:

```env
# Flask
FLASK_SECRET_KEY=your-secret-key-here
IS_HTTPS=true|false

# Database
DB_PATH=tickets.db

# Session
SESSION_TIMEOUT_SECONDS=3600

# SMTP Email
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_SSL=false
SMTP_USE_TLS=true
SMTP_SENDER_EMAIL=noreply@example.com
SMTP_SENDER_NAME=TicketX System

# App
APP_BASE_URL=https://ticketx.yourdomain.com/
IT_MAILBOX=it@yourdomain.com

# Notifications
EXCLUDE_ACTOR_FROM_NOTIFICATIONS=true
NOTIFY_IT_ADMINS_WHEN_ASSIGNED_TO_IT=true
ENABLE_ASYNC_EMAIL=true

# Google API (if integrated)
GOOGLE_API_KEY=your-key-here
```

---

## Deployment on IIS

The application is deployed on IIS using **wfastcgi** module:

1. **Python Installation**: Python 3.x installed on server
2. **Virtual Environment**: Created and activated
3. **Dependencies**: All packages installed via `pip install -r requirements.txt`
4. **wfastcgi Configuration**: IIS FastCGI module configured to route to Flask app
5. **Environment Variables**: `.env` file placed in application root
6. **URL Rewrite Module**: IIS rewrite rules configured for Flask routing
7. **Session Cookies**: HTTPS-enforced when `IS_HTTPS=true`

---

## Security Features

1. **Password Security**:
   - werkzeug PBKDF2-SHA256 (salted) hashing
   - Backward-compatible migration of legacy SHA-256 hashes on login
   - No plaintext storage

2. **CSRF Protection**:
   - Flask-WTF `CSRFProtect` on all state-changing requests
   - Tokens auto-injected into forms and AJAX/`fetch` calls

3. **File Upload Security**:
   - Whitelist validation (extension + MIME type)
   - Size limits (10MB max)
   - Filename obfuscation
   - Content-type verification

4. **Session Security**:
   - HttpOnly cookies
   - SameSite attributes
   - Configurable timeout
   - HTTPS enforcement option

5. **SQL Injection Prevention**:
   - Parameterized queries throughout
   - Status filter whitelisting in models
   - SQLite context managers for safe connection handling

6. **Secrets Management**:
   - `.env` (and databases/uploads) excluded from version control via `.gitignore`
   - `.env.example` template provided for configuration

7. **Access Control**:
   - Role-based decorators (@login_required, @admin_required)
   - Department-head authorization
   - Private ticket support

---

## Performance Considerations

- **Per-request connections + WAL**: Each request gets its own SQLite connection, closed automatically on teardown; WAL mode allows concurrent readers/writer
- **Async Email**: Background thread processing prevents request blocking
- **Caching**: SQLite PRAGMA cache_size set to 64MB
- **Indices**: 15+ optimized indices on frequently queried columns
- **WAL Mode**: Improved concurrency for multiple concurrent operations

---

## Development & Maintenance

### Key Modules

- **app.py**: Main application with 47+ route handlers
- **models.py**: ORM-like data models (UserModel, TicketModel, CategoryModel)
- **database.py**: SQLite connection management and schema initialization
- **auth.py**: Decorators for authentication and authorization
- **notifications.py**: Email notification system (635 lines)
- **mailer.py**: SMTP integration with fallbacks

### Database Utilities

- **migration.sql**: Database indices and optimization
- **migration2.py**: Schema update tools
- **db_check.py**: Database validation
- **delete_db.py**: Development database reset

### Data Import/Export

- **Pandas-based Excel import** for bulk user loading
- **Download support** for tickets and attachments

---

## Logging

- **Flask Logging**: Error and warning logs captured
- **Audit Trail**: All user actions logged with timestamps and IP addresses
- **Activity Log**: Comprehensive ticket history

---

## Summary

**TicketX** is a production-ready ticket management system with:
- Enterprise-grade architecture
- Comprehensive audit logging
- Role-based access control
- Multi-department support
- Email notification system
- File attachment handling
- Optimized SQLite (WAL mode, per-request connections)
- Secure session management
- IIS deployment capability

The system provides a complete solution for ticket lifecycle management across organizations with multiple departments and users.
