from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, date, timedelta
import json
import requests
from anthropic import Anthropic
from dotenv import load_dotenv
import os

load_dotenv()
print("API Key loaded:", os.getenv('ANTHROPIC_API_KEY')[:5] if os.getenv('ANTHROPIC_API_KEY') else "No API key found")

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

def calculate_protein_goal(weight_lbs, ratio):
    weight_kg = weight_lbs * 0.453592
    return weight_kg * ratio

def get_llm_nutrition_estimate(meal_description):
    anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    
    prompt = f"""You are analyzing a meal to estimate its nutritional content. Break down each component and provide protein and calorie estimates.
    
    Meal: {meal_description}
    
    Rules:
    1. Always provide realistic estimates even with vague portions
    2. Round protein to nearest 0.5g
    3. Round calories to nearest 10
    4. If portion is unclear, assume a typical serving size
    
    Provide your response in this exact JSON format:
    {{
        "total": {{
            "protein": 0,
            "calories": 0
        }},
        "breakdown": [
            {{
                "item": "food name",
                "portion": "amount",
                "protein": 0,
                "calories": 0
            }}
        ]
    }}"""
    
    try:
        message = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=1000,
            temperature=0,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        response_text = message.content[0].text
        return json.loads(response_text)
        
    except Exception as e:
        print(f"Error getting nutrition estimate: {e}")
        return None

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
                         saved_meals=saved_meals,
                         entry_type=request.args.get('entry_type', 'manual'))

@app.route('/analyze_meal', methods=['POST'])
def analyze_meal():
    try:
        meal_description = request.json.get('description')
        if not meal_description:
            return jsonify({"error": "No meal description provided"}), 400
        
        nutrition_data = get_llm_nutrition_estimate(meal_description)
        
        if nutrition_data:
            return jsonify({
                "message": "Meal analyzed successfully",
                "nutrition": nutrition_data
            }), 200
        else:
            return jsonify({"error": "Could not analyze meal"}), 500
    except Exception as e:
        print("Error in analyze_meal:", str(e))
        return jsonify({"error": str(e)}), 500

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
    else:
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

@app.route('/workouts')
def workouts():
    today = date.today()
    worked_out_today = Workout.query.filter(
        Workout.date >= today,
        Workout.date < datetime.combine(today, datetime.max.time())
    ).first() is not None
    
    workouts = Workout.query.order_by(Workout.date.desc()).limit(10).all()
    return render_template('workouts.html',
                         active_tab='workouts',
                         date=today.strftime("%B %d, %Y"),
                         worked_out_today=worked_out_today,
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

@app.route('/history')
def history():
    end_date = date.today()
    start_date = end_date - timedelta(days=29)
    
    nutrition_entries = NutritionEntry.query.filter(
        NutritionEntry.date >= start_date,
        NutritionEntry.date <= end_date
    ).order_by(NutritionEntry.date.asc()).all()
    
    workouts = Workout.query.filter(
        Workout.date >= datetime.combine(start_date, datetime.min.time()),
        Workout.date <= datetime.combine(end_date, datetime.max.time())
    ).order_by(Workout.date.desc()).all()
    
    settings = UserSettings.query.first()
    protein_goal = calculate_protein_goal(settings.weight_lbs, settings.protein_ratio)
    
    chart_data = []
    current_date = start_date
    while current_date <= end_date:
        day_nutrition = [e for e in nutrition_entries if e.date == current_date]
        protein_total = sum(entry.protein_amount for entry in day_nutrition)
        calorie_total = sum(entry.calorie_amount for entry in day_nutrition)
        
        chart_data.append({
            'day': (current_date - start_date).days + 1,
            'protein': protein_total if protein_total > 0 else None,
            'calories': calorie_total if calorie_total > 0 else None
        })
        current_date += timedelta(days=1)
    
    history = []
    current_date = end_date
    while current_date >= start_date:
        day_nutrition = [e for e in nutrition_entries if e.date == current_date]
        protein_total = sum(entry.protein_amount for entry in day_nutrition)
        calorie_total = sum(entry.calorie_amount for entry in day_nutrition)
        
        day_workout = next((w for w in workouts if w.date.date() == current_date), None)
        
        history.append({
            'date': current_date,
            'nutrition': {
                'protein': protein_total,
                'calories': calorie_total,
                'protein_goal': protein_goal
            } if day_nutrition else None,
            'workout': {
                'type': day_workout.type,
                'exercises': json.loads(day_workout.exercises) if day_workout else []
            } if day_workout else None
        })
        current_date -= timedelta(days=1)
    
    return render_template('history.html',
                         active_tab='history',
                         history=history,
                         protein_goal=protein_goal,
                         chart_data=chart_data)  

@app.route('/settings')
def settings():
    settings = UserSettings.query.first()
    if not settings:
        settings = UserSettings(weight_lbs=195, protein_ratio=1.5)
        db.session.add(settings)
        db.session.commit()
    
    return render_template('settings.html',
                         active_tab='settings',
                         weight=settings.weight_lbs,
                         ratio=settings.protein_ratio)

@app.route('/saved_meals', methods=['GET', 'POST'])
def saved_meals():
    if request.method == 'POST':
        data = request.json
        if not all(key in data for key in ['name', 'protein_per_serving', 'calories_per_serving']):
            return jsonify({"error": "Missing required fields"}), 400
        
        try:
            new_meal = SavedMeal(
                name=data['name'],
                protein_per_serving=float(data['protein_per_serving']),
                calories_per_serving=int(data['calories_per_serving'])
            )
            db.session.add(new_meal)
            db.session.commit()
            return jsonify({"message": "Meal saved successfully", "id": new_meal.id}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500
            
    saved_meals = SavedMeal.query.all()
    return jsonify([{
        "id": meal.id,
        "name": meal.name,
        "protein_per_serving": meal.protein_per_serving,
        "calories_per_serving": meal.calories_per_serving
    } for meal in saved_meals])

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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5001)