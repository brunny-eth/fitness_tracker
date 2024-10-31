from flask import Flask, request, jsonify, render_template
import json
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, date
import requests

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fitness_tracker.db'
db = SQLAlchemy(app)
migrate = Migrate(app, db)

class UserSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    weight_lbs = db.Column(db.Float, nullable=False)
    protein_ratio = db.Column(db.Float, nullable=False)  

class SavedMeal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    protein_per_serving = db.Column(db.Float, nullable=False)
    calories_per_serving = db.Column(db.Integer, nullable=False)

class NutritionEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    protein_amount = db.Column(db.Float, nullable=False)
    calorie_amount = db.Column(db.Integer, nullable=False)
    meal_name = db.Column(db.String(100))  

class Workout(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    type = db.Column(db.String(50), nullable=False)
    exercises = db.Column(db.Text, nullable=False)  

def get_protein_from_wger(ingredient_name):
    url = "https://wger.de/api/v2/ingredient/"
    params = {"name": ingredient_name}
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        if data['results']:
            result = data['results'][0]
            return {
                'name': result['name'],
                'protein': result['protein'],
                'calories': result.get('energy', 0)  
            }
    return None

def calculate_protein_goal(weight_lbs, ratio):
    weight_kg = weight_lbs * 0.453592  
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
    
    entries = NutritionEntry.query.filter_by(date=today).all()
    total_protein = sum(entry.protein_amount for entry in entries)
    total_calories = sum(entry.calorie_amount for entry in entries)
    
    saved_meals = SavedMeal.query.all()
    
    return render_template('nutrition.html', 
                           active_tab='nutrition', 
                           date=today.strftime("%B %d, %Y"),
                           total_protein=total_protein,
                           total_calories=total_calories,
                           goal_amount=round(protein_goal, 1),
                           protein_goal_reached=total_protein >= protein_goal,
                           weight=settings.weight_lbs,
                           ratio=settings.protein_ratio,
                           saved_meals=saved_meals)

@app.route('/add_nutrition', methods=['POST'])
def add_nutrition():
    data = request.json
    
    if data.get('saved_meal_id'):
        meal = SavedMeal.query.get_or_404(data['saved_meal_id'])
        new_entry = NutritionEntry(
            date=date.today(),
            protein_amount=meal.protein_per_serving,
            calorie_amount=meal.calories_per_serving,
            meal_name=meal.name
        )
    elif data.get('ingredient'):
        # Handle ingredient lookup from API
        ingredient_data = get_protein_from_wger(data['ingredient'])
        if ingredient_data is None:
            return jsonify({"error": "Ingredient not found"}), 404
        new_entry = NutritionEntry(
            date=date.today(),
            protein_amount=ingredient_data['protein'],
            calorie_amount=ingredient_data['calories'],
            meal_name=ingredient_data['name']
        )
    else:
        # Handle manual entry
        if not data.get('protein_amount') or not data.get('calorie_amount'):
            return jsonify({"error": "Both protein and calorie amounts must be provided"}), 400
        new_entry = NutritionEntry(
            date=date.today(),
            protein_amount=float(data['protein_amount']),
            calorie_amount=int(data['calorie_amount']),
            meal_name=data.get('meal_name', 'Manual entry')
        )
    
    db.session.add(new_entry)
    db.session.commit()
    return jsonify({"message": "Nutrition entry added successfully"}), 201

@app.route('/saved_meals', methods=['POST'])
def add_saved_meal():
    data = request.json
    if not all(key in data for key in ['name', 'protein_per_serving', 'calories_per_serving']):
        return jsonify({"error": "Missing required fields"}), 400
    
    new_meal = SavedMeal(
        name=data['name'],
        protein_per_serving=float(data['protein_per_serving']),
        calories_per_serving=int(data['calories_per_serving'])
    )
    db.session.add(new_meal)
    db.session.commit()
    return jsonify({"message": "Meal saved successfully"}), 201

@app.route('/update_settings', methods=['POST'])
def update_settings():
    data = request.json
    if not all(key in data for key in ['weight', 'ratio']):  
        return jsonify({"error": "Weight and ratio must be provided"}), 400
    
    settings = UserSettings.query.first()
    if settings:
        settings.weight_lbs = float(data['weight'])
        settings.protein_ratio = float(data['ratio'])
    else:
        settings = UserSettings(
            weight_lbs=float(data['weight']),
            protein_ratio=float(data['ratio'])
        )
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

@app.route('/delete_workout/<int:workout_id>', methods=['POST'])
def delete_workout(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    db.session.delete(workout)
    db.session.commit()
    return jsonify({"message": "Workout deleted successfully"}), 200

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)

    