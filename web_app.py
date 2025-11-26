#!/usr/bin/env python3
"""
Web-App für TWS Signal Service Dashboard.
Startet die Flask-Anwendung.
"""

from tws_bot.web import app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)