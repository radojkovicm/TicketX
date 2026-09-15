from database import Database
from security import verify_password, hash_password, is_legacy_hash
import hashlib
import os
import logging
from datetime import datetime
from contextlib import contextmanager

class UserModel:
    """
    User model with secure authentication and connection management.
    """
    
    def __init__(self):
        self.db = Database()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for safe connection handling"""
        conn = self.db.get_connection()
        try:
            yield conn
        finally:
            conn.close()
    
    def authenticate(self, username, password):
        """
        Authenticate user with username and password.
        
        Args:
            username: Username string
            password: Plain text password (will be hashed)
            
        Returns:
            User tuple if authenticated, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.*, d.name as department_name
                FROM users u
                LEFT JOIN departments d ON u.department_id = d.id
                WHERE u.username = ?
            """, (username,))
            user = cursor.fetchone()

            if not user:
                return None

            stored_hash = user[2]  # kolona 'password'
            if not verify_password(stored_hash, password):
                return None

            # Migracija: ako je stari nesoljeni SHA-256 hash, re-hesuj ga.
            if is_legacy_hash(stored_hash):
                try:
                    cursor.execute(
                        "UPDATE users SET password = ? WHERE id = ?",
                        (hash_password(password), user[0])
                    )
                    conn.commit()
                except Exception as e:
                    logging.warning(f"Password rehash failed for user {user[0]}: {e}")

            return user
    
    def get_user_by_id(self, user_id):
        """Get user details by ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.*, d.name as department_name
                FROM users u
                LEFT JOIN departments d ON u.department_id = d.id
                WHERE u.id = ?
            """, (user_id,))
            return cursor.fetchone()
    
    def get_department_users(self, department_id):
        """Get all users in a department"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM users
                WHERE department_id = ? AND role = 'user'
                ORDER BY full_name
            """, (department_id,))
            return cursor.fetchall()
    
    def add_watcher(self, ticket_id, user_id):
        """Add a watcher to a ticket"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id) VALUES (?, ?)",
                (ticket_id, user_id)
            )
            conn.commit()
    
    def remove_watcher(self, ticket_id, user_id):
        """Remove a watcher from a ticket"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM ticket_watchers WHERE ticket_id = ? AND user_id = ?",
                (ticket_id, user_id)
            )
            conn.commit()
    
    def get_watchers_for_ticket(self, ticket_id):
        """Get all watchers for a ticket"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.id, u.full_name 
                FROM users u
                JOIN ticket_watchers tw ON u.id = tw.user_id
                WHERE tw.ticket_id = ?
                ORDER BY u.full_name
            """, (ticket_id,))
            return cursor.fetchall()
    
    def get_tickets_watched_by_user(self, user_id):
        """Get all tickets watched by a user"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.* 
                FROM tickets t
                JOIN ticket_watchers tw ON t.id = tw.ticket_id
                WHERE tw.user_id = ?
                ORDER BY t.updated_at DESC
            """, (user_id,))
            return cursor.fetchall()


class TicketModel:
    """
    Ticket model with SQL injection protection and secure queries.
    """
    
    # Whitelist for valid status filters (SQL injection protection)
    VALID_STATUS_FILTERS = {'active', 'closed', 'all'}
    
    def __init__(self):
        self.db = Database()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for safe connection handling"""
        conn = self.db.get_connection()
        try:
            yield conn
        finally:
            conn.close()
    
    def _validate_status_filter(self, status_filter):
        """
        Validate status filter against whitelist.
        
        Args:
            status_filter: Status filter string
            
        Returns:
            Validated status filter or 'active' as default
        """
        if status_filter not in self.VALID_STATUS_FILTERS:
            logging.warning(f"Invalid status filter: {status_filter}, defaulting to 'active'")
            return 'active'
        return status_filter
    
    def create_ticket(self, title, description, category_id, priority, created_by):
        """Create a new ticket"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tickets (title, description, category_id, priority, created_by, status)
                VALUES (?, ?, ?, ?, ?, 'new')
            """, (title, description, category_id, priority, created_by))
            ticket_id = cursor.lastrowid
            conn.commit()
            return ticket_id
    
    def get_user_tickets(self, user_id):
        """Get all tickets created by a user"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.*, c.name as category_name, u.full_name as created_by_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                WHERE t.created_by = ?
                ORDER BY t.created_at DESC
            """, (user_id,))
            return cursor.fetchall()
    
    def get_all_tickets(self):
        """Get all tickets"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.*, c.name as category_name, u.full_name as created_by_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                ORDER BY t.created_at DESC
            """)
            return cursor.fetchall()
    
    def get_department_tickets(self, department_id):
        """Get all tickets from a department"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.*, c.name as category_name, u.full_name as created_by_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                WHERE u.department_id = ?
                ORDER BY t.created_at DESC
            """, (department_id,))
            return cursor.fetchall()
    
    def update_ticket_status(self, ticket_id, status):
        """
        Update ticket status with configurable timezone.
        
        Args:
            ticket_id: Ticket ID
            status: New status
        """
        # Get timezone from environment or use Europe/Belgrade (Serbia)
        timezone_str = os.getenv('TIMEZONE', 'Europe/Belgrade')
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            try:
                import pytz
                tz = pytz.timezone(timezone_str)
                updated_at = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                logging.warning(f"Timezone error: {e}, using system time")
                updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute("""
                UPDATE tickets
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status, updated_at, ticket_id))
            conn.commit()
    
    def get_user_tickets_filtered(self, user_id, status_filter='active'):
        """
        Get user tickets with status filter (SQL injection protected).
        
        Args:
            user_id: User ID
            status_filter: One of 'active', 'closed', 'all'
            
        Returns:
            List of tickets
        """
        status_filter = self._validate_status_filter(status_filter)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Build query with parameterized status condition
            base_query = """
                SELECT t.*, c.name as category_name, u.full_name as created_by_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                WHERE t.created_by = ?
            """
            
            params = [user_id]
            
            if status_filter == 'active':
                base_query += " AND t.status != ?"
                params.append('closed')
            elif status_filter == 'closed':
                base_query += " AND t.status = ?"
                params.append('closed')
            # 'all' - no additional filter
            
            base_query += " ORDER BY t.created_at DESC"
            
            cursor.execute(base_query, params)
            return cursor.fetchall()
    
    def get_all_tickets_filtered(self, status_filter='active'):
        """
        Get all tickets with status filter (SQL injection protected).
        
        Args:
            status_filter: One of 'active', 'closed', 'all'
            
        Returns:
            List of tickets
        """
        status_filter = self._validate_status_filter(status_filter)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            base_query = """
                SELECT t.*, c.name as category_name, u.full_name as created_by_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
            """
            
            params = []
            
            if status_filter == 'active':
                base_query += " WHERE t.status != ?"
                params.append('closed')
            elif status_filter == 'closed':
                base_query += " WHERE t.status = ?"
                params.append('closed')
            # 'all' - no WHERE clause
            
            base_query += " ORDER BY t.created_at DESC"
            
            cursor.execute(base_query, params)
            return cursor.fetchall()
    
    def get_department_head_tickets_filtered(self, department_id, user_id, status_filter='active'):
        """
        Get department head tickets with status filter (SQL injection protected).
        
        Args:
            department_id: Department ID
            user_id: Current user ID
            status_filter: One of 'active', 'closed', 'all'
            
        Returns:
            List of tickets
        """
        status_filter = self._validate_status_filter(status_filter)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            base_query = """
                SELECT t.*, c.name as category_name, u.full_name as created_by_name,
                       CASE WHEN t.created_by = ? THEN 'own' ELSE 'team' END as ticket_type
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                WHERE (u.department_id = ? OR t.created_by = ?)
            """
            
            params = [user_id, department_id, user_id]
            
            if status_filter == 'active':
                base_query += " AND t.status != ?"
                params.append('closed')
            elif status_filter == 'closed':
                base_query += " AND t.status = ?"
                params.append('closed')
            # 'all' - no additional filter
            
            base_query += """
                ORDER BY 
                    CASE WHEN t.created_by = ? THEN 0 ELSE 1 END,
                    t.created_at DESC
            """
            params.append(user_id)
            
            cursor.execute(base_query, params)
            return cursor.fetchall()
    
    def get_all_tickets_filtered_assigned(self, status_filter='active', assigned_filter='all', current_user_id=None):
        """
        Get tickets with status and assignment filters (SQL injection protected).
        
        Args:
            status_filter: One of 'active', 'closed', 'all'
            assigned_filter: 'me', 'unassigned', 'all', or user_id
            current_user_id: Current user ID
            
        Returns:
            List of tickets
        """
        status_filter = self._validate_status_filter(status_filter)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            base_query = """
                SELECT t.*, c.name as category_name, u.full_name as created_by_name, 
                       a.full_name as assigned_to_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                LEFT JOIN users a ON t.assigned_to = a.id
                WHERE 1=1
            """
            
            params = []
            
            # Status filter
            if status_filter == 'active':
                base_query += " AND t.status != ?"
                params.append('closed')
            elif status_filter == 'closed':
                base_query += " AND t.status = ?"
                params.append('closed')
            
            # Assignment filter
            if assigned_filter == 'me' and current_user_id:
                base_query += " AND t.assigned_to = ?"
                params.append(current_user_id)
            elif assigned_filter == 'unassigned':
                base_query += " AND t.assigned_to IS NULL"
            elif assigned_filter != 'all':
                # Validate it's a number before using
                try:
                    assigned_id = int(assigned_filter)
                    base_query += " AND t.assigned_to = ?"
                    params.append(assigned_id)
                except ValueError:
                    logging.warning(f"Invalid assigned_filter: {assigned_filter}")
            
            base_query += " ORDER BY t.created_at DESC"
            
            cursor.execute(base_query, params)
            return cursor.fetchall()


class CategoryModel:
    """
    Category model for ticket categorization.
    """
    
    def __init__(self):
        self.db = Database()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for safe connection handling"""
        conn = self.db.get_connection()
        try:
            yield conn
        finally:
            conn.close()
    
    def get_all_categories(self):
        """Get all categories ordered by name"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories ORDER BY name")
            return cursor.fetchall()
    
    def get_category_by_id(self, category_id):
        """Get category by ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
            return cursor.fetchone()
    
    def create_category(self, name, description=''):
        """Create a new category"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO categories (name, description) VALUES (?, ?)",
                (name, description)
            )
            conn.commit()
            return cursor.lastrowid
    
    def update_category(self, category_id, name, description=''):
        """Update a category"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE categories SET name = ?, description = ? WHERE id = ?",
                (name, description, category_id)
            )
            conn.commit()
    
    def delete_category(self, category_id):
        """Delete a category"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            conn.commit()
