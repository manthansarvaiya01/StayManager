from app import app, db

with app.app_context():
    # Drop all tables and recreate them
    db.drop_all()
    db.create_all()
    print("Database tables recreated successfully!")