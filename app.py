from flask import Flask, request, jsonify, render_template
import json
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
import requests

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fitness_tracker.db'
db = SQLAlchemy(app)

class UserSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    weight_lbs = db.Column(db.Float, nullable=False)
    protein_ratio = db.Column(db.Float, nullable=False)  # g per kg of body weight

class ProteinEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Float, nullable=False)

class Workout(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    type = db.Column(db.String(50), nullable=False)
    exercises = db.Column(db.Text, nullable=False)  # Store as JSON string

def get_protein_from_wger(ingredient_name):
    url = "https://wger.de/api/v2/ingredient/"
    params = {"name": ingredient_name}
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        if data['results']:
            return data['results'][0]
    return None

def calculate_protein_goal(weight_lbs, ratio):
    weight_kg = weight_lbs * 0.453592  # Convert lbs to kg
    return weight_kg * ratio

@app.route('/')
def home():
    settings = UserSettings.query.first()
    if not settings:
        settings = UserSettings(weight_lbs=195, protein_ratio=1.5)
        db.session.add(settings)
        db.session.commit()
    
    protein_goal = calculate_protein_goal(settings.weight_lbs, settings.protein_ratio)
    today = date.today()
    total_protein = sum(entry.amount for entry in ProteinEntry.query.filter_by(date=today).all())
    goal_reached = total_protein >= protein_goal
    
    return render_template('nutrition.html', 
                           active_tab='nutrition', 
                           date=today.strftime("%B %d, %Y"),
                           total_protein=total_protein,
                           goal_amount=round(protein_goal, 1),
                           goal_reached=goal_reached,
                           weight=settings.weight_lbs,
                           ratio=settings.protein_ratio)

@app.route('/add_protein', methods=['POST'])
def add_protein():
    data = request.json
    ingredient_name = data.get('ingredient')
    if ingredient_name:
        ingredient_data = get_protein_from_wger(ingredient_name)
        if ingredient_data is None:
            return jsonify({"error": "Ingredient not found"}), 404
        protein_amount = ingredient_data['protein']
        return jsonify({
            "name": ingredient_data['name'],
            "protein": protein_amount,
            "message": f"Found {protein_amount}g of protein in {ingredient_name}"
        }), 200
    else:
        protein_amount = data.get('amount')
    
    if protein_amount is None:
        return jsonify({"error": "Protein amount not provided"}), 400
    
    new_entry = ProteinEntry(date=date.today(), amount=float(protein_amount))
    db.session.add(new_entry)
    db.session.commit()
    return jsonify({"message": "Protein entry added successfully", "amount": protein_amount}), 201

@app.route('/update_settings', methods=['POST'])
def update_settings():
    data = request.json
    weight = data.get('weight')
    ratio = data.get('ratio')
    if not weight or not ratio:
        return jsonify({"error": "Weight and ratio must be provided"}), 400
    
    settings = UserSettings.query.first()
    if settings:
        settings.weight_lbs = float(weight)
        settings.protein_ratio = float(ratio)
    else:
        settings = UserSettings(weight_lbs=float(weight), protein_ratio=float(ratio))
        db.session.add(settings)
    db.session.commit()
    return jsonify({"message": "Settings updated successfully"}), 200

@app.route('/workouts')
def workouts():
    workouts = Workout.query.order_by(Workout.date.desc()).limit(10).all()
    return render_template('workouts.html', 
                         active_tab='workouts', 
                         workouts=workouts,
                         json=json)  

@app.route('/log_workout', methods=['POST'])
def log_workout():
    data = request.json
    new_workout = Workout(
        type=data['type'],
        exercises=json.dumps(data['exercises'])
    )
    db.session.add(new_workout)
    db.session.commit()
    return jsonify({"message": "Workout logged successfully"}), 201

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)

    