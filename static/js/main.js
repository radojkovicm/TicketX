// Main JavaScript for ticket system
window.addEventListener('beforeunload', function() {
    console.log('Page unloading - check if logout is called');
});

document.addEventListener('DOMContentLoaded', function() {
    // Auto-hide alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });

    // Confirm delete actions
    const deleteButtons = document.querySelectorAll('[data-confirm]');
    deleteButtons.forEach(function(button) {
        button.addEventListener('click', function(e) {
            const message = this.getAttribute('data-confirm');
            if (!confirm(message)) {
                e.preventDefault();
            }
        });
    });

    // File upload preview
    const fileInput = document.getElementById('attachment');
    if (fileInput) {
        fileInput.addEventListener('change', function() {
            const file = this.files[0];
            if (file) {
                const fileSize = (file.size / 1024 / 1024).toFixed(2);
                const maxSize = 10; // 10MB
                
                if (fileSize > maxSize) {
                    alert(`File size (${fileSize}MB) exceeds maximum allowed size (${maxSize}MB)`);
                    this.value = '';
                    return;
                }
                
                // Show file info
                const fileInfo = document.createElement('div');
                fileInfo.className = 'mt-2 text-muted';
                fileInfo.innerHTML = `Selected: ${file.name} (${fileSize}MB)`;
                
                // Remove existing file info
                const existingInfo = this.parentNode.querySelector('.file-info');
                if (existingInfo) {
                    existingInfo.remove();
                }
                
                fileInfo.className += ' file-info';
                this.parentNode.appendChild(fileInfo);
            }
        });
    }

    // Status update confirmations
    const statusButtons = document.querySelectorAll('button[name="status"]');
    statusButtons.forEach(function(button) {
        button.addEventListener('click', function(e) {
            const status = this.value;
            let message = '';
            
            switch(status) {
                case 'in_progress':
                    message = 'Start working on this ticket?';
                    break;
                case 'awaiting_confirmation':
                    message = 'Mark this ticket as completed and request user confirmation?';
                    break;
                case 'closed':
                    message = 'Close this ticket?';
                    break;
            }
            
            if (message && !confirm(message)) {
                e.preventDefault();
            }
        });
    });

   // Auto-refresh dashboard every 30 seconds (bez promene aktivnog taba)
    if (window.location.pathname === '/dashboard') {
        setInterval(function() {
            // Only refresh if user is still active (no modals open, etc.)
            if (!document.querySelector('.modal.show')) {
                // Sačuvaj trenutni aktivni tab
                const activeTab = document.querySelector('#ticketTabs .nav-link.active');
                const activeTabId = activeTab ? activeTab.id : null;
                
                // Sačuvaj u localStorage
                if (activeTabId) {
                    localStorage.setItem('activeTicketTab', activeTabId);
                }
                
                location.reload();
            }
        }, 30000);
    }

    // Search functionality
    const searchInput = document.getElementById('ticket-search');
    if (searchInput) {
        searchInput.addEventListener('input', function() {
            const searchTerm = this.value.toLowerCase();
            const tableRows = document.querySelectorAll('tbody tr');
            
            tableRows.forEach(function(row) {
                const text = row.textContent.toLowerCase();
                if (text.includes(searchTerm)) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
        });
    }

    // Form validation
    const forms = document.querySelectorAll('form[data-validate]');
    forms.forEach(function(form) {
        form.addEventListener('submit', function(e) {
            const requiredFields = form.querySelectorAll('[required]');
            let isValid = true;
            
            requiredFields.forEach(function(field) {
                if (!field.value.trim()) {
                    field.classList.add('is-invalid');
                    isValid = false;
                } else {
                    field.classList.remove('is-invalid');
                }
            });
            
            if (!isValid) {
                e.preventDefault();
                alert('Please fill in all required fields.');
            }
        });
    });

    // Tooltip initialization
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
});

// Utility functions
function showLoading(button) {
    const originalText = button.innerHTML;
    button.innerHTML = '<span class="spinner"></span> Loading...';
    button.disabled = true;
    
    return function() {
        button.innerHTML = originalText;
        button.disabled = false;
    };
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
}

// AJAX helper for future use
function makeRequest(url, method = 'GET', data = null) {
    return fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
        },
        body: data ? JSON.stringify(data) : null
    })
    .then(response => response.json())
    .catch(error => {
        console.error('Request failed:', error);
        throw error;
    })
};

document.addEventListener('DOMContentLoaded', function () {
    const watcherInput = document.getElementById('watchers');
    if (!watcherInput) return;

    let currentFocus = -1;

    watcherInput.addEventListener('input', function () {
        const val = this.value;
        closeAllLists();
        if (!val) return false;

        // Uzmi poslednji deo unosa posle poslednjeg zareza i trimuj
        const parts = val.split(',');
        const lastPart = parts[parts.length - 1].trim();

        if (!lastPart) return false;

        fetch(`/api/users?query=${encodeURIComponent(lastPart)}`)
            .then(response => response.json())
            .then(users => {
                if (!users.length) return false;

                const list = document.createElement('div');
                list.setAttribute('id', this.id + '-autocomplete-list');
                list.setAttribute('class', 'autocomplete-items');
                this.parentNode.appendChild(list);

                users.forEach(user => {
                    const item = document.createElement('div');
                    item.innerHTML = "<strong>" + user.substr(0, lastPart.length) + "</strong>";
                    item.innerHTML += user.substr(lastPart.length);
                    item.innerHTML += "<input type='hidden' value='" + user + "'>";
                    item.addEventListener('click', () => {
                        // Zameni samo poslednji deo sa izabranim korisnikom
                        parts[parts.length - 1] = user;
                        watcherInput.value = parts.join(', ') + ', ';
                        closeAllLists();
                    });
                    list.appendChild(item);
                });
            });
    });

    watcherInput.addEventListener('keydown', function (e) {
        let list = document.getElementById(this.id + '-autocomplete-list');
        if (list) list = list.getElementsByTagName('div');
        if (e.keyCode == 40) { // down
            currentFocus++;
            addActive(list);
        } else if (e.keyCode == 38) { // up
            currentFocus--;
            addActive(list);
        } else if (e.keyCode == 13) { // enter
            e.preventDefault();
            if (currentFocus > -1) {
                if (list) list[currentFocus].click();
            }
        }
    });

    function addActive(list) {
        if (!list) return false;
        removeActive(list);
        if (currentFocus >= list.length) currentFocus = 0;
        if (currentFocus < 0) currentFocus = (list.length - 1);
        list[currentFocus].classList.add('autocomplete-active');
    }

    function removeActive(list) {
        for (let i = 0; i < list.length; i++) {
            list[i].classList.remove('autocomplete-active');
        }
    }

    function closeAllLists(elmnt) {
        const items = document.getElementsByClassName('autocomplete-items');
        for (let i = 0; i < items.length; i++) {
            if (elmnt != items[i] && elmnt != watcherInput) {
                items[i].parentNode.removeChild(items[i]);
            }
        }
        currentFocus = -1;
    }

    document.addEventListener('click', function (e) {
        closeAllLists(e.target);
    });
});