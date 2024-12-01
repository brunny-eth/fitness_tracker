import os
import logging
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import json
import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
print("API Key loaded:", os.getenv('ANTHROPIC_API_KEY')[:5] if os.getenv('ANTHROPIC_API_KEY') else "No API key found")

if not os.path.exists('logs'):
    os.mkdir('logs')
logging.basicConfig(
    filename='logs/app.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)
logging.info("Application startup")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fitness_tracker.db'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
db = SQLAlchemy(app)
migrate = Migrate(app, db)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    settings = db.relationship('UserSettings', backref='user', uselist=False)
    saved_meals = db.relationship('SavedMeal', backref='user')
    nutrition_entries = db.relationship('NutritionEntry', backref='user')
    workouts = db.relationship('Workout', backref='user')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class UserSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    weight_lbs = db.Column(db.Float, nullable=False)
    protein_ratio = db.Column(db.Float, nullable=False)
    max_calories = db.Column(db.Integer, nullable=False, default=2500)
    
    start_date = db.Column(db.DateTime, nullable=True) 
    starting_weight = db.Column(db.Float, nullable=True)
    age = db.Column(db.Integer, nullable=True)
    gender = db.Column(db.String(10), nullable=True)
    height_inches = db.Column(db.Float, nullable=True)
    
    target_weight = db.Column(db.Float, nullable=True)
    goal_months = db.Column(db.Integer, nullable=True)

class WeightEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    weight = db.Column(db.Float, nullable=False)
    
    user = db.relationship('User', backref='weight_entries')

class SavedMeal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    protein_per_serving = db.Column(db.Float, nullable=False)
    calories_per_serving = db.Column(db.Integer, nullable=False)

class NutritionEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    protein_amount = db.Column(db.Float, nullable=False)
    calorie_amount = db.Column(db.Integer, nullable=False)
    meal_name = db.Column(db.String(100))

class Workout(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    type = db.Column(db.String(50), nullable=False)
    exercises = db.Column(db.Text, nullable=False)

class WorkoutCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False) 
    exercises = db.Column(db.Text, nullable=False) 
    user = db.relationship('User', backref='workout_categories')

    def get_exercises(self):
        return json.loads(self.exercises)

    def set_exercises(self, exercises_list):
        self.exercises = json.dumps(exercises_list)

def calculate_protein_goal(weight_lbs, ratio):
    weight_kg = weight_lbs * 0.453592
    return round(weight_kg * ratio) 

def create_default_workout_categories(user_id):
    default_categories = [
        {
            "name": "High Intensity Interval Training",
            "exercises": ["4x4 Run"]
        },
        {
            "name": "Upper Body",
            "exercises": [
                "Bench Press",
                "Shoulder Press", 
                "Bicep Curls",
                "Tricep Pulldowns"
            ]
        },
        {
            "name": "Lower Body",
            "exercises": [
                "Squat",
                "Calf Raises",
                "Deadlifts"
            ]
        },
        {
            "name": "Abs",
            "exercises": [
                "Crunches",
                "Planks"
            ]
        }
    ]
    
    for category in default_categories:
        workout_category = WorkoutCategory(
            user_id=user_id,
            name=category["name"],
            exercises=json.dumps(category["exercises"])
        )
        db.session.add(workout_category)

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
def landing():
    if current_user.is_authenticated:
        return redirect(url_for('nutrition'))
    return render_template('homepage.html')

@app.route('/nutrition')
@login_required
def nutrition():
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        settings = UserSettings(user_id=current_user.id, weight_lbs=195, protein_ratio=1.5)
        db.session.add(settings)
        db.session.commit()
    
    latest_weight = WeightEntry.query.filter_by(
        user_id=current_user.id
    ).order_by(WeightEntry.date.desc()).first()
    
    current_weight = latest_weight.weight if latest_weight else settings.weight_lbs
    protein_goal = calculate_protein_goal(current_weight, settings.protein_ratio)
    
    today = date.today()
    
    entries = NutritionEntry.query.filter_by(
        user_id=current_user.id,
        date=today
    ).order_by(NutritionEntry.id.asc()).all()
    
    saved_meals = SavedMeal.query.filter_by(user_id=current_user.id).all()
    
    total_protein = sum(entry.protein_amount for entry in entries)
    total_calories = sum(entry.calorie_amount for entry in entries)

    todays_weight = WeightEntry.query.filter_by(
        user_id=current_user.id,
        date=today
    ).first()

    return render_template('nutrition.html',
                         active_tab='nutrition',
                         date=today.strftime("%B %d, %Y"),
                         total_protein=total_protein,
                         total_calories=total_calories,
                         goal_amount=round(protein_goal, 1),
                         max_calories=settings.max_calories,
                         protein_goal_reached=total_protein >= protein_goal,
                         calories_exceeded=total_calories > settings.max_calories,
                         weight=settings.weight_lbs,
                         ratio=settings.protein_ratio,
                         saved_meals=saved_meals,
                         entries=entries,  
                         todays_weight=todays_weight,
                         entry_type=request.args.get('entry_type', 'manual'))

@app.route('/analyze_meal', methods=['POST'])
@login_required
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
@login_required
def add_nutrition():
    try:
        data = request.json
        logging.info(f"Received nutrition data: {data}")
        
        if data.get('protein_amount') and float(data['protein_amount']) < 0:
            return jsonify({"error": "Protein amount cannot be negative"}), 400
        if data.get('calorie_amount') and int(data['calorie_amount']) < 0:
            return jsonify({"error": "Calorie amount cannot be negative"}), 400

        if data.get('saved_meal_id'):
            meal = SavedMeal.query.filter_by(
                id=data['saved_meal_id'],
                user_id=current_user.id
            ).first_or_404()
            new_entry = NutritionEntry(
                user_id=current_user.id,
                date=date.today(),
                protein_amount=meal.protein_per_serving,
                calorie_amount=meal.calories_per_serving,
                meal_name=meal.name
            )
        else:
            new_entry = NutritionEntry(
                user_id=current_user.id,
                date=date.today(),
                protein_amount=float(data['protein_amount']),
                calorie_amount=int(data['calorie_amount']),
                meal_name=data.get('meal_name', 'Manual entry')
            )

        db.session.add(new_entry)
        db.session.commit()
        logging.info("Successfully added nutrition entry")
        return jsonify({"message": "Nutrition entry added successfully"}), 201
        
    except Exception as e:
        logging.error(f"Error adding nutrition: {str(e)}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/workouts')
@login_required
def workouts():
    today = date.today()
    
    todays_workout = Workout.query.filter(
        Workout.user_id == current_user.id,
        Workout.date >= today,
        Workout.date < datetime.combine(today, datetime.max.time())
    ).first()
    
    worked_out_today = todays_workout is not None
    
    workout_summary = None
    if todays_workout:
        exercises = json.loads(todays_workout.exercises)
        workout_summary = f"{todays_workout.type}; {len(exercises)} exercises"
    
    workout_categories = WorkoutCategory.query.filter_by(user_id=current_user.id).all()
    
    for category in workout_categories:
        last_workout = Workout.query.filter_by(
            user_id=current_user.id,
            type=category.name
        ).order_by(Workout.date.desc()).first()
        
        if last_workout:
            category.last_completed = last_workout.date
        else:
            category.last_completed = None
    
    return render_template('workouts.html',
                         active_tab='workouts',
                         date=today.strftime("%B %d, %Y"),
                         worked_out_today=worked_out_today,
                         workout_summary=workout_summary,
                         workout_categories=workout_categories,
                         json=json)

@app.route('/log_workout', methods=['POST'])
@login_required
def log_workout():
    data = request.json
    new_workout = Workout(
        user_id=current_user.id,
        type=data['type'],
        exercises=json.dumps(data['exercises'])
    )
    db.session.add(new_workout)
    db.session.commit()
    return jsonify({"message": "Workout logged successfully"}), 201

@app.route('/add_weight', methods=['POST'])
@login_required
def add_weight():
    try:
        data = request.json
        today = date.today()
        
        existing_entry = WeightEntry.query.filter_by(
            user_id=current_user.id,
            date=today
        ).first()
        
        if existing_entry:
            return jsonify({
                "error": f"Already logged weight of {(existing_entry.weight * 0.453592):.1f} kg today"
            }), 400
            
        weight_kg = float(data['weight'])
        weight_lbs = weight_kg * 2.20462  
        
        entry = WeightEntry(
            user_id=current_user.id,
            date=today,
            weight=weight_lbs  
        )
        
        db.session.add(entry)
        db.session.commit()
        
        return jsonify({"message": "Weight saved successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/saved_meals', methods=['GET', 'POST'])
@login_required
def saved_meals():
    if request.method == 'POST':
        data = request.json
        try:
            protein = float(data['protein_per_serving'])
            calories = int(data['calories_per_serving'])
            
            if protein < 0 or calories < 0:
                return jsonify({"error": "Protein and calories cannot be negative"}), 400
                
            new_meal = SavedMeal(
                user_id=current_user.id,
                name=data['name'],
                protein_per_serving=protein,
                calories_per_serving=calories
            )
            db.session.add(new_meal)
            db.session.commit()
            return jsonify({"message": "Meal saved successfully", "id": new_meal.id}), 201
        
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500
            
    saved_meals = SavedMeal.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        "id": meal.id,
        "name": meal.name,
        "protein_per_serving": meal.protein_per_serving,
        "calories_per_serving": meal.calories_per_serving
    } for meal in saved_meals])

@app.route('/history')
@login_required
def history():
    end_date = date.today()
    start_date = end_date - timedelta(days=29)
    
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        flash('Please configure your settings first')
        return redirect(url_for('settings'))
    
    protein_goal = calculate_protein_goal(settings.weight_lbs, settings.protein_ratio)
    
    weight_entries = WeightEntry.query.filter(
        WeightEntry.user_id == current_user.id,
        WeightEntry.date >= start_date,
        WeightEntry.date <= end_date
    ).all()
    
    latest_weight_entry = WeightEntry.query.filter_by(
        user_id=current_user.id
    ).order_by(WeightEntry.date.desc()).first()
    
    nutrition_entries = NutritionEntry.query.filter(
        NutritionEntry.user_id == current_user.id,
        NutritionEntry.date >= start_date,
        NutritionEntry.date <= end_date
    ).all()
    
    workouts = Workout.query.filter(
        Workout.user_id == current_user.id,
        Workout.date >= datetime.combine(start_date, datetime.min.time()),
        Workout.date <= datetime.combine(end_date, datetime.max.time())
    ).all()
    
    nutrition_by_date = {}
    for entry in nutrition_entries:
        if entry.date not in nutrition_by_date:
            nutrition_by_date[entry.date] = {'protein': 0, 'calories': 0}
        nutrition_by_date[entry.date]['protein'] += entry.protein_amount
        nutrition_by_date[entry.date]['calories'] += entry.calorie_amount
    
    weight_by_date = {entry.date: entry.weight for entry in weight_entries}
    
    chart_data = []
    current_date = start_date
    while current_date <= end_date:
        nutrition = nutrition_by_date.get(current_date, {'protein': None, 'calories': None})
        chart_data.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'protein': nutrition['protein'],
            'calories': nutrition['calories'],
            'weight': weight_by_date.get(current_date)
        })
        current_date += timedelta(days=1)
    
    progress = None
    total_change = None
    current_change = None
    if latest_weight_entry and settings.target_weight and settings.starting_weight:
        latest_kg = latest_weight_entry.weight * 0.453592
        target_kg = settings.target_weight * 0.453592
        starting_kg = settings.starting_weight * 0.453592
        
        total_change = target_kg - starting_kg
        if total_change != 0:
            current_change = latest_kg - starting_kg
            progress = round((current_change / total_change) * 100, 1)
    
    history = []
    current_date = end_date
    while current_date >= start_date:
        nutrition = nutrition_by_date.get(current_date)
        day_workout = next((w for w in workouts if w.date.date() == current_date), None)
        
        if nutrition or day_workout:
            history.append({
                'date': current_date,
                'nutrition': {
                    'protein': nutrition['protein'] if nutrition else 0,
                    'calories': nutrition['calories'] if nutrition else 0,
                    'protein_goal': protein_goal
                } if nutrition else None,
                'workout': {
                    'type': day_workout.type,
                    'exercises': json.loads(day_workout.exercises)
                } if day_workout else None
            })
        current_date -= timedelta(days=1)
    
    print(f"Date range: {start_date} to {end_date}")
    print(f"Chart data points: {len(chart_data)}")
    print(f"Sample data point: {chart_data[0] if chart_data else 'No data'}")
    
    return render_template('history.html',
                         active_tab='history',
                         history=history,
                         chart_data=chart_data,
                         protein_goal=protein_goal,
                         settings=settings,
                         max_calories=settings.max_calories,
                         latest_weight_entry=latest_weight_entry,
                         progress=progress,
                         total_change=total_change,
                         current_change=current_change)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('nutrition'))
        
        flash('Invalid email or password')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            email = request.form.get('email')
            password = request.form.get('password')
            
            if User.query.filter_by(email=email).first():
                flash('Email already registered')
                return redirect(url_for('register'))
            
            user = User(email=email)
            user.set_password(password)
            
            starting_weight_kg = float(request.form.get('starting_weight'))
            target_weight_kg = float(request.form.get('target_weight'))
            height_cm = float(request.form.get('height'))
            
            starting_weight_lbs = starting_weight_kg * 2.20462
            target_weight_lbs = target_weight_kg * 2.20462
            height_inches = height_cm / 2.54
            
            goal_months = int(request.form.get('goal_months'))
            maintenance_calories = int(request.form.get('maintenance_calories'))
            
            weight_diff_kg = starting_weight_kg - target_weight_kg
            total_calorie_deficit_needed = weight_diff_kg * 7700
            days_to_goal = goal_months * 30.44 
            
            daily_deficit = total_calorie_deficit_needed / days_to_goal
            target_daily_calories = maintenance_calories - daily_deficit
            target_daily_calories = round(target_daily_calories / 50) * 50
            
            protein_goal = request.form.get('protein_goal')
            if protein_goal == 'high':
                protein_ratio = 1.6
            elif protein_goal == 'medium':
                protein_ratio = 1.3
            else:  
                protein_ratio = 1.0

            settings = UserSettings(
                user=user,
                weight_lbs=starting_weight_lbs,
                starting_weight=starting_weight_lbs,
                target_weight=target_weight_lbs,
                goal_months=goal_months,
                age=int(request.form.get('age')),
                gender=request.form.get('gender'),
                height_inches=height_inches,
                protein_ratio=protein_ratio,
                max_calories=int(target_daily_calories),
                start_date=datetime.utcnow()
            )
            
            db.session.add(user)
            db.session.add(settings)
            db.session.commit()

            create_default_workout_categories(user.id)
            db.session.commit()


            login_user(user)
            return redirect(url_for('nutrition'))
            
        except Exception as e:
            db.session.rollback()
            flash('Error during registration. Please try again.')
            print(f"Registration error: {str(e)}")
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/settings')
@login_required
def settings():
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        settings = UserSettings(
            user_id=current_user.id,
            weight_lbs=195,
            protein_ratio=1.5,
            max_calories=2500,
            start_date=datetime.utcnow(),  
            starting_weight=195
        )
        db.session.add(settings)
        db.session.commit()
    
    latest_weight_entry = WeightEntry.query.filter_by(
        user_id=current_user.id
    ).order_by(WeightEntry.date.desc()).first()
    
    latest_weight = latest_weight_entry.weight if latest_weight_entry else settings.weight_lbs
    
    progress = None
    if settings.target_weight and settings.starting_weight:
        total_change_needed = abs(settings.target_weight - settings.starting_weight)
        if total_change_needed > 0:
            current_change = abs(latest_weight - settings.starting_weight)
            progress = round((current_change / total_change_needed) * 100, 1)
    
    return render_template('settings.html',
                         active_tab='settings',
                         settings=settings,
                         latest_weight=latest_weight,
                         progress=progress)

@app.route('/update_settings', methods=['POST'])
@login_required
def update_settings():
    data = request.json
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    
    try:
        if not settings.start_date:
            settings.start_date = datetime.utcnow()
            
        if data.get('weight') and data['weight'].strip():
            settings.weight_lbs = float(data['weight'])
        if data.get('ratio') and data['ratio'].strip():
            settings.protein_ratio = float(data['ratio'])
        if data.get('max_calories') and data['max_calories'].strip():
            settings.max_calories = int(data['max_calories'])
            
        if data.get('starting_weight') and data['starting_weight'].strip():
            settings.starting_weight = float(data['starting_weight'])
        if data.get('starting_bodyfat') and data['starting_bodyfat'].strip():
            settings.starting_bodyfat = float(data['starting_bodyfat'])
        if data.get('age') and data['age'].strip():
            settings.age = int(data['age'])
        if data.get('gender'):
            settings.gender = data['gender']
        if data.get('height_inches') and data['height_inches'].strip():
            settings.height_inches = float(data['height_inches'])
            
        if data.get('target_weight') and data['target_weight'].strip():
            settings.target_weight = float(data['target_weight'])
        if data.get('target_bodyfat') and data['target_bodyfat'].strip():
            settings.target_bodyfat = float(data['target_bodyfat'])
        if data.get('goal_months') and data['goal_months'].strip():
            settings.goal_months = int(data['goal_months'])
            
        db.session.commit()
        return jsonify({"message": "Settings updated successfully"}), 200
        
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": "Please enter valid numbers for all fields"}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/delete_nutrition/<int:entry_id>', methods=['POST'])
@login_required
def delete_nutrition(entry_id):
    entry = NutritionEntry.query.filter_by(
        id=entry_id,
        user_id=current_user.id
    ).first_or_404()
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"message": "Entry deleted successfully"}), 200

@app.route('/get_workout_categories')
@login_required 
def get_workout_categories():
    categories = WorkoutCategory.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'name': cat.name,
        'exercises': cat.get_exercises()  
    } for cat in categories])

@app.route('/get_saved_meal/<int:meal_id>', methods=['GET'])
@login_required
def get_saved_meal(meal_id):
    meal = SavedMeal.query.filter_by(
        id=meal_id,
        user_id=current_user.id
    ).first_or_404()
    
    return jsonify({
        'id': meal.id,
        'name': meal.name,
        'protein_per_serving': meal.protein_per_serving,
        'calories_per_serving': meal.calories_per_serving
    })

@app.route('/update_workout_category', methods=['POST'])
@login_required  
def update_workout_category():
    data = request.json
    if not data.get('name') or not data.get('exercises'):
        return jsonify({"error": "Name and exercises are required"}), 400
    
    if data.get('id'):
        category = WorkoutCategory.query.filter_by(
            id=data['id'],
            user_id=current_user.id
        ).first()
        if not category:
            return jsonify({"error": "Category not found"}), 404
    else:
        category = WorkoutCategory.query.filter_by(
            name=data['name'],
            user_id=current_user.id
        ).first()
    
    try:
        if category:
            category.name = data['name']
            category.exercises = json.dumps(data['exercises'])
        else:
            category = WorkoutCategory(
                user_id=current_user.id,
                name=data['name'],
                exercises=json.dumps(data['exercises'])
            )
            db.session.add(category)
        
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/delete_saved_meal/<int:meal_id>', methods=['POST'])
@login_required
def delete_saved_meal(meal_id):
    meal = SavedMeal.query.filter_by(
        id=meal_id,
        user_id=current_user.id
    ).first_or_404()
    
    db.session.delete(meal)
    db.session.commit()
    return jsonify({"message": "Meal deleted successfully"}), 200

@app.route('/delete_workout_category/<int:category_id>', methods=['POST'])
@login_required  
def delete_workout_category(category_id):
    category = WorkoutCategory.query.filter_by(
        id=category_id,
        user_id=current_user.id
    ).first_or_404()
    
    try:
        db.session.delete(category)
        db.session.commit()
        return jsonify({"success": True, "message": "Category deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting workout category: {str(e)}")
        return jsonify({"success": False, "error": "Error deleting category"}), 500

@app.route('/delete_workout/<int:workout_id>', methods=['POST'])
@login_required
def delete_workout(workout_id):
    workout = Workout.query.filter_by(
        id=workout_id,
        user_id=current_user.id
    ).first_or_404()
    
    try:
        db.session.delete(workout)
        db.session.commit()
        return jsonify({"message": "Workout deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting workout: {str(e)}")
        return jsonify({"error": "Error deleting workout"}), 500

@app.route('/update_nutrition', methods=['POST'])
@login_required
def update_nutrition():
    data = request.json
    try:
        date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        
        entry = NutritionEntry.query.filter_by(
            user_id=current_user.id,
            date=date
        ).first()
        
        if entry:
            entry.protein_amount = float(data['protein'])
            entry.calorie_amount = int(data['calories'])
        else:
            entry = NutritionEntry(
                user_id=current_user.id,
                date=date,
                protein_amount=float(data['protein']),
                calorie_amount=int(data['calories']),
                meal_name='Manual update'
            )
            db.session.add(entry)
            
        db.session.commit()
        return jsonify({"message": "Nutrition data updated successfully"}), 200
        
    except Exception as e:
        logging.error(f"Error updating nutrition: {str(e)}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/get_workout_category/<int:category_id>')
@login_required
def get_workout_category(category_id):
    category = WorkoutCategory.query.filter_by(
        id=category_id,
        user_id=current_user.id
    ).first_or_404()
    
    return jsonify({
        'id': category.id,
        'name': category.name,
        'exercises': category.get_exercises()
    })

@app.route('/get_last_workout/<workout_type>')
@login_required
def get_last_workout(workout_type):
    last_workout = Workout.query.filter_by(
        user_id=current_user.id,
        type=workout_type
    ).order_by(Workout.date.desc()).first()
    
    if last_workout:
        return jsonify({
            "type": last_workout.type,
            "exercises": json.loads(last_workout.exercises)
        }), 200
    return jsonify({"message": "No previous workout found"}), 404

@app.route('/saved_meals/<int:meal_id>', methods=['PUT'])
@login_required
def update_saved_meal(meal_id):
    try:
        data = request.json
        meal = SavedMeal.query.filter_by(
            id=meal_id,
            user_id=current_user.id
        ).first_or_404()
        
        meal.name = data['name']
        meal.protein_per_serving = float(data['protein_per_serving'])
        meal.calories_per_serving = int(data['calories_per_serving'])
        
        db.session.commit()
        return jsonify({"message": "Meal updated successfully", "id": meal.id}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/get_exercise_history/<workout_type>/<exercise_name>')
@login_required
def get_exercise_history(workout_type, exercise_name):
    last_workout = Workout.query.filter_by(
        user_id=current_user.id,
        type=workout_type
    ).order_by(Workout.date.desc()).first()
    
    if last_workout:
        exercises = json.loads(last_workout.exercises)
        for exercise in exercises:
            if exercise['name'] == exercise_name:
                return jsonify(exercise), 200
    
    return jsonify({"message": "No history found"}), 404

@app.route('/update_workout/<int:workout_id>', methods=['POST'])
@login_required
def update_workout(workout_id):
    try:
        data = request.json
        workout = Workout.query.filter_by(
            id=workout_id,
            user_id=current_user.id
        ).first_or_404()
        
        workout.type = data['type']
        workout.exercises = json.dumps(data['exercises'])
        
        db.session.commit()
        return jsonify({"message": "Workout updated successfully"}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating workout: {str(e)}")
        return jsonify({"error": str(e)}), 500

# app runner
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5001)