from database import Database
import hashlib
from datetime import datetime

class UserModel:
    def __init__(self):
        self.db = Database()

    def authenticate(self, username, password):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        cursor.execute("""
            SELECT u.*, d.name as department_name
            FROM users u
            LEFT JOIN departments d ON u.department_id = d.id
            WHERE u.username = ? AND u.password = ?
        """, (username, hashed_password))

        user = cursor.fetchone()
        conn.close()
        return user

    def get_user_by_id(self, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT u.*, d.name as department_name
            FROM users u
            LEFT JOIN departments d ON u.department_id = d.id
            WHERE u.id = ?
        """, (user_id,))

        user = cursor.fetchone()
        conn.close()
        return user

    def get_department_users(self, department_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM users
            WHERE department_id = ? AND role = 'user'
        """, (department_id,))

        users = cursor.fetchall()
        conn.close()
        return users
    
    def add_watcher(self, ticket_id, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id) VALUES (?, ?)", (ticket_id, user_id))
        conn.commit()
        conn.close()

    def remove_watcher(self, ticket_id, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ticket_watchers WHERE ticket_id = ? AND user_id = ?", (ticket_id, user_id))
        conn.commit()
        conn.close()

    def get_watchers_for_ticket(self, ticket_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.id, u.full_name FROM users u
            JOIN ticket_watchers tw ON u.id = tw.user_id
            WHERE tw.ticket_id = ?
        """, (ticket_id,))
        watchers = cursor.fetchall()
        conn.close()
        return watchers

    def get_tickets_watched_by_user(self, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.* FROM tickets t
            JOIN ticket_watchers tw ON t.id = tw.ticket_id
            WHERE tw.user_id = ?
        """, (user_id,))
        tickets = cursor.fetchall()
        conn.close()
        return tickets

class TicketModel:
    def __init__(self):
        self.db = Database()

    def create_ticket(self, title, description, category_id, priority, created_by):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO tickets (title, description, category_id, priority, created_by, status)
            VALUES (?, ?, ?, ?, ?, 'assigned')
        """, (title, description, category_id, priority, created_by))

        ticket_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return ticket_id

    def get_user_tickets(self, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT t.*, c.name as category_name, u.full_name as created_by_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            WHERE t.created_by = ?
            ORDER BY t.created_at DESC
        """, (user_id,))

        tickets = cursor.fetchall()
        conn.close()
        return tickets

    def get_all_tickets(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT t.*, c.name as category_name, u.full_name as created_by_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            ORDER BY t.created_at DESC
        """)

        tickets = cursor.fetchall()
        conn.close()
        return tickets

    def get_department_tickets(self, department_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT t.*, c.name as category_name, u.full_name as created_by_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            WHERE u.department_id = ?
            ORDER BY t.created_at DESC
        """, (department_id,))

        tickets = cursor.fetchall()
        conn.close()
        return tickets

    def update_ticket_status(self, ticket_id, status):
        from datetime import datetime
        import pytz
        
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        # Get current time in Slovenia timezone
        slovenia_tz = pytz.timezone('Europe/Ljubljana')
        updated_at = datetime.now(slovenia_tz).strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute("""
            UPDATE tickets
            SET status = ?, updated_at = ?
            WHERE id = ?
        """, (status, updated_at, ticket_id))

        conn.commit()
        conn.close()
        
    def get_department_head_tickets_filtered(self, department_id, user_id, status_filter='active'):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        # Base query
        base_query = """
            SELECT t.*, c.name as category_name, u.full_name as created_by_name, a.full_name as assigned_to_name,
                CASE
                    WHEN t.created_by = ? THEN 'own'
                    ELSE 'team'
                END as ticket_type
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            LEFT JOIN users a ON t.assigned_to = a.id
            WHERE (t.created_by = ? OR t.created_by IN (
                SELECT id FROM users WHERE department_id = ? AND id != ?
            ))
        """

        # Add status filter
        if status_filter == 'active':
            base_query += " AND t.status NOT IN ('closed')"
        elif status_filter == 'closed':
            base_query += " AND t.status = 'closed'"
        # 'all' - no additional filter

        base_query += " ORDER BY t.created_at DESC"

        cursor.execute(base_query, (user_id, user_id, department_id, user_id))
        tickets = cursor.fetchall()
        conn.close()

        return tickets
    
    def get_user_tickets_filtered(self, user_id, status_filter='active'):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        if status_filter == 'active':
            status_condition = "AND t.status != 'closed'"
        elif status_filter == 'closed':
            status_condition = "AND t.status = 'closed'"
        else:  # all
            status_condition = ""

        cursor.execute(f"""
            SELECT t.*, c.name as category_name, u.full_name as created_by_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            WHERE t.created_by = ? {status_condition}
            ORDER BY t.created_at DESC
        """, (user_id,))

        tickets = cursor.fetchall()
        conn.close()
        return tickets

    def get_all_tickets_filtered(self, status_filter='active'):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        if status_filter == 'active':
            status_condition = "WHERE t.status != 'closed'"
        elif status_filter == 'closed':
            status_condition = "WHERE t.status = 'closed'"
        else:  # all
            status_condition = ""

        cursor.execute(f"""
            SELECT t.*, c.name as category_name, u.full_name as created_by_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            {status_condition}
            ORDER BY t.created_at DESC
        """)

        tickets = cursor.fetchall()
        conn.close()
        return tickets

    def get_department_head_tickets_filtered(self, department_id, user_id, status_filter='active'):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        if status_filter == 'active':
            status_condition = "AND t.status != 'closed'"
        elif status_filter == 'closed':
            status_condition = "AND t.status = 'closed'"
        else:  # all
            status_condition = ""

        cursor.execute(f"""
            SELECT t.*, c.name as category_name, u.full_name as created_by_name,
                CASE WHEN t.created_by = ? THEN 'own' ELSE 'team' END as ticket_type
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            WHERE (u.department_id = ? OR t.created_by = ?) {status_condition}
            ORDER BY
                CASE WHEN t.created_by = ? THEN 0 ELSE 1 END,
                t.created_at DESC
        """, (user_id, department_id, user_id, user_id))

        tickets = cursor.fetchall()
        conn.close()
        return tickets
    
    def get_all_tickets_filtered_assigned(self, status_filter='active', assigned_filter='all', current_user_id=None):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        base_query = """
            SELECT t.*, c.name as category_name, u.full_name as created_by_name, a.full_name as assigned_to_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            LEFT JOIN users a ON t.assigned_to = a.id
            WHERE 1=1
        """

        params = []

        # Status filter
        if status_filter == 'active':
            base_query += " AND t.status NOT IN ('closed')"
        elif status_filter == 'closed':
            base_query += " AND t.status = 'closed'"

        # Assignment filter
        if assigned_filter == 'me':
            base_query += " AND t.assigned_to = ?"
            params.append(current_user_id)
        elif assigned_filter == 'unassigned':
            base_query += " AND t.assigned_to IS NULL"
        elif assigned_filter != 'all' and assigned_filter.isdigit():
            base_query += " AND t.assigned_to = ?"
            params.append(int(assigned_filter))

        base_query += " ORDER BY t.created_at DESC"

        cursor.execute(base_query, params)
        tickets = cursor.fetchall()
        conn.close()
        return tickets
        
        def add_watcher(self, ticket_id, user_id):
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id) VALUES (?, ?)", (ticket_id, user_id))
            conn.commit()
            conn.close()

        def remove_watcher(self, ticket_id, user_id):
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ticket_watchers WHERE ticket_id = ? AND user_id = ?", (ticket_id, user_id))
            conn.commit()
            conn.close()

        def get_watchers_for_ticket(self, ticket_id):
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.id, u.full_name FROM users u
                JOIN ticket_watchers tw ON u.id = tw.user_id
                WHERE tw.ticket_id = ?
            """, (ticket_id,))
            watchers = cursor.fetchall()
            conn.close()
            return watchers

        def get_tickets_watched_by_user(self, user_id):
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.* FROM tickets t
                JOIN ticket_watchers tw ON t.id = tw.ticket_id
                WHERE tw.user_id = ?
            """, (user_id,))
            tickets = cursor.fetchall()
            conn.close()
            return tickets

        conn.commit()
        conn.close()

class CategoryModel:
    def __init__(self):
        self.db = Database()

    def get_all_categories(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM categories ORDER BY name")
        categories = cursor.fetchall()
        conn.close()
        return categories