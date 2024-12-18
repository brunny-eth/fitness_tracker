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
    
    current_weight_kg = db.Column(db.Float, nullable=False)
    target_weight_kg = db.Column(db.Float, nullable=False)
    starting_weight_kg = db.Column(db.Float, nullable=False)
    
    protein_ratio = db.Column(db.Float, nullable=False)
    max_calories = db.Column(db.Integer, nullable=False, default=2500)
    start_date = db.Column(db.DateTime, nullable=False)
    goal_months = db.Column(db.Integer, nullable=False)

    activity_level = db.Column(db.String(20), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    height_inches = db.Column(db.Float, nullable=False)
    age = db.Column(db.Integer, nullable=False)

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

def determine_weight_direction(current_weight_kg, target_weight_kg):
    """
    Determines if user is trying to lose or gain weight
    Returns: 'loss' if trying to lose weight, 'gain' if trying to gain
    """
    if current_weight_kg > target_weight_kg:
        return 'loss'
    elif current_weight_kg < target_weight_kg:
        return 'gain'
    return 'maintain'

def calculate_protein_goal(weight_kg, ratio, direction='loss'):
    """
    Calculate protein goal based on weight and direction
    For weight loss: use target weight for calculation
    For weight gain: use current weight + 10% for muscle growth support
    """
    if direction == 'gain':
        weight_for_calculation = weight_kg * 1.1  
    else:
        weight_for_calculation = weight_kg
        
    return round(weight_for_calculation * ratio)

def calculate_calorie_target(current_weight_kg, settings):
    """Calculate daily calorie target based on current weight and goals"""
    try:
        if not all([
            settings.gender,
            settings.height_inches,
            settings.age,
            settings.activity_level,
            settings.target_weight_kg,
            settings.goal_months,
            settings.start_date
        ]):
            raise ValueError("Missing required settings")
            
        goal_date = settings.start_date + timedelta(days=settings.goal_months * 30.44)
        days_remaining = (goal_date - datetime.utcnow()).days
        
        if days_remaining <= 0:
            raise ValueError("Goal date has passed")
            
        height_cm = settings.height_inches * 2.54
        if settings.gender == 'male':
            bmr = 88.362 + (13.397 * current_weight_kg) + (4.799 * height_cm) - (5.677 * settings.age)
        else:
            bmr = 447.593 + (9.247 * current_weight_kg) + (3.098 * height_cm) - (4.330 * settings.age)
            
        activity_multipliers = {
            'sedentary': 1.2,
            'light': 1.375,
            'moderate': 1.55,
            'heavy': 1.725,
            'athlete': 1.9
        }
        
        maintenance = bmr * activity_multipliers[settings.activity_level]
        
        direction = determine_weight_direction(current_weight_kg, settings.target_weight_kg)
        total_weight_change = abs(current_weight_kg - settings.target_weight_kg)
        total_calories_needed = total_weight_change * 7700  # 7700 calories per kg
        daily_caloric_change = total_calories_needed / days_remaining
        
        if direction == 'gain':
            target_calories = round((maintenance + daily_caloric_change) / 50) * 50
        else:  
            target_calories = round((maintenance - daily_caloric_change) / 50) * 50
        
        return max(1200, target_calories)
        
    except Exception as e:
        logging.error(f"Error calculating calories: {str(e)}")
        raise

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
        return redirect(url_for('register'))
        
    print("Debug - Settings object:", settings)  
    print("Debug - Settings attributes:", {
        'target_weight': settings.target_weight_kg,
        'current_weight': settings.current_weight_kg,
        'starting_weight': settings.starting_weight_kg
    })

    # Get latest weight entry for today    
    today = date.today()
    todays_weight = WeightEntry.query.filter_by(
        user_id=current_user.id,
        date=today
    ).first()
    
    # Get nutrition entries for today
    entries = NutritionEntry.query.filter_by(
        user_id=current_user.id,
        date=today
    ).order_by(NutritionEntry.id.asc()).all()
    
    # Calculate totals
    total_protein = sum(entry.protein_amount for entry in entries)
    total_calories = sum(entry.calorie_amount for entry in entries)

    # Calculate goals based on current weight and direction
    current_weight_kg = todays_weight.weight if todays_weight else settings.current_weight_kg
    weight_direction = determine_weight_direction(current_weight_kg, settings.target_weight_kg)
    
    # Calculate protein goal considering direction
    protein_goal = calculate_protein_goal(
        settings.target_weight_kg, 
        settings.protein_ratio,
        direction=weight_direction
    )
    
    saved_meals = SavedMeal.query.filter_by(user_id=current_user.id).all()

    return render_template('nutrition.html',
                         active_tab='nutrition',
                         date=today.strftime("%B %d, %Y"),
                         settings=settings,
                         total_protein=total_protein,
                         total_calories=total_calories,
                         goal_amount=round(protein_goal, 1),
                         max_calories=settings.max_calories,
                         weight_direction=weight_direction,
                         protein_goal_reached=total_protein >= protein_goal,
                         calories_exceeded=(total_calories > settings.max_calories) if weight_direction == 'loss' 
                                        else (total_calories < settings.max_calories),
                         current_weight_kg=current_weight_kg,
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
    
    def workouts_on_date(target_date):
        return Workout.query.filter(
            Workout.user_id == current_user.id,
            Workout.date >= datetime.combine(target_date, datetime.min.time()),
            Workout.date < datetime.combine(target_date, datetime.max.time())
        ).order_by(Workout.date.desc()).all()
    
    todays_workouts = workouts_on_date(today)
    worked_out_today = len(todays_workouts) > 0 
        
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
    
    last_workout = Workout.query.filter(
        Workout.user_id == current_user.id
    ).order_by(Workout.date.desc()).first()

    return render_template('workouts.html',
                         active_tab='workouts',
                         date=today.strftime("%B %d, %Y"),
                         worked_out_today=worked_out_today,
                         todays_workouts=todays_workouts,
                         workout_categories=workout_categories,
                         workouts_on_date=workouts_on_date,
                         now=datetime.now(),
                         last_workout=last_workout,  
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
        
        if 'weight' not in data:
            return jsonify({"error": "Weight is required"}), 400
            
        weight_kg = float(data['weight'])
        
        existing_entry = WeightEntry.query.filter_by(
            user_id=current_user.id,
            date=today
        ).first()
        
        if existing_entry:
            return jsonify({
                "error": f"Already logged weight of {existing_entry.weight:.1f} kg today"
            }), 400
            
        entry = WeightEntry(
            user_id=current_user.id,
            date=today,
            weight=weight_kg
        )
        
        settings = UserSettings.query.filter_by(user_id=current_user.id).first()
        settings.current_weight_kg = weight_kg
        
        # Determine new direction based on latest weight
        weight_direction = determine_weight_direction(weight_kg, settings.target_weight_kg)
        
        # Calculate new calorie target based on current weight and direction
        new_calories = calculate_calorie_target(
            current_weight_kg=weight_kg,
            settings=settings
        )
        
        settings.max_calories = new_calories
        
        db.session.add(entry)
        db.session.commit()
        
        return jsonify({
            "message": "Weight saved successfully",
            "new_calories_target": new_calories,
            "weight_direction": weight_direction
        }), 200
        
    except ValueError:
        return jsonify({"error": "Please enter a valid weight"}), 400
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
    
    # Get latest weight for direction calculation
    latest_weight_entry = WeightEntry.query.filter_by(
        user_id=current_user.id
    ).order_by(WeightEntry.date.desc()).first()
    
    current_weight_kg = latest_weight_entry.weight if latest_weight_entry else settings.current_weight_kg
    weight_direction = determine_weight_direction(current_weight_kg, settings.target_weight_kg)
    
    # Calculate protein goal with direction
    protein_goal = calculate_protein_goal(
        settings.target_weight_kg, 
        settings.protein_ratio,
        direction=weight_direction
    )
    
    weight_entries = WeightEntry.query.filter(
        WeightEntry.user_id == current_user.id,
        WeightEntry.date >= start_date,
        WeightEntry.date <= end_date
    ).all()
    
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
    
    history = []
    current_date = end_date
    while current_date >= start_date:
        nutrition = nutrition_by_date.get(current_date)
        day_workout = next((w for w in workouts if w.date.date() == current_date), None)
        
        if nutrition:
            calories_status = (
                'under_goal' if weight_direction == 'gain' and nutrition['calories'] < settings.max_calories else
                'over_goal' if weight_direction == 'loss' and nutrition['calories'] > settings.max_calories else
                'on_target'
            )
        else:
            calories_status = None
            
        history.append({
            'date': current_date,
            'nutrition': {
                'protein': nutrition['protein'] if nutrition else 0,
                'calories': nutrition['calories'] if nutrition else 0,
                'protein_goal': protein_goal,
                'calories_status': calories_status
            },  
            'workout': {
                'type': day_workout.type,
                'exercises': json.loads(day_workout.exercises)
            } if day_workout else None
        })
        current_date -= timedelta(days=1)
    
    # Calculate progress differently based on direction
    progress = None
    total_change = None
    current_change = None
    if latest_weight_entry and settings.target_weight_kg and settings.starting_weight_kg:
        latest_kg = latest_weight_entry.weight
        target_kg = settings.target_weight_kg
        starting_kg = settings.starting_weight_kg
        
        # Calculate changes based on direction
        if weight_direction == 'loss':
            total_change = starting_kg - target_kg  # Positive number for weight loss goal
            current_change = starting_kg - latest_kg  # Positive number means weight lost
        else:  # gain or maintain
            total_change = target_kg - starting_kg  # Positive number for weight gain goal
            current_change = latest_kg - starting_kg  # Positive number means weight gained
            
        if total_change != 0:
            progress = round((current_change / total_change) * 100, 1)
            # Cap progress at 100% if goal is exceeded
            progress = min(progress, 100) if progress > 0 else max(progress, -100)
    
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
                         current_change=current_change,
                         weight_direction=weight_direction)

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
            # Basic validation
            required_fields = [
                'email', 'password', 'starting_weight', 'target_weight',
                'goal_months', 'activity_level', 'age', 'gender', 'height'
            ]
            
            for field in required_fields:
                if not request.form.get(field):
                    flash(f'{field.replace("_", " ").title()} is required')
                    return redirect(url_for('register'))

            # Create user first
            user = User(email=request.form.get('email'))
            user.set_password(request.form.get('password'))
            
            if User.query.filter_by(email=user.email).first():
                flash('Email already registered')
                return redirect(url_for('register'))

            db.session.add(user)
            db.session.commit()

            # Get settings data
            starting_weight_kg = float(request.form.get('starting_weight'))
            target_weight_kg = float(request.form.get('target_weight'))
            
            # Determine initial direction
            weight_direction = determine_weight_direction(starting_weight_kg, target_weight_kg)
            
            goal_months = int(request.form.get('goal_months'))
            activity_level = request.form.get('activity_level')
            age = int(request.form.get('age'))
            gender = request.form.get('gender')
            height_cm = float(request.form.get('height'))
            
            # Get protein ratio based on direction
            protein_goal = request.form.get('protein_goal', 'medium')
            if protein_goal == 'high':
                protein_ratio = 1.6
            elif protein_goal == 'medium':
                protein_ratio = 1.3
            else:
                protein_ratio = 1.0

            # Create settings
            settings = UserSettings(
                user=user,
                current_weight_kg=starting_weight_kg,
                starting_weight_kg=starting_weight_kg,
                target_weight_kg=target_weight_kg,
                activity_level=activity_level,
                goal_months=goal_months,
                age=age,
                gender=gender,
                height_inches=height_cm / 2.54,
                protein_ratio=protein_ratio,
                start_date=datetime.utcnow()
            )
            
            # Calculate initial calories considering direction
            try:
                initial_calories = calculate_calorie_target(
                    current_weight_kg=starting_weight_kg,
                    settings=settings
                )
                settings.max_calories = initial_calories
            except Exception as e:
                print(f"Calorie calculation error: {str(e)}")
                # Set a reasonable default based on direction
                settings.max_calories = 2000 if weight_direction == 'loss' else 2800

            db.session.add(settings)
            db.session.commit()

            create_default_workout_categories(user.id)
            db.session.commit()

            login_user(user)
            return redirect(url_for('nutrition'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Registration failed with error: {str(e)}")
            flash('Error during registration. Please try again.')
            return redirect(url_for('register'))
        
    return render_template('register.html')

@app.route('/fix_settings', methods=['POST'])
@login_required
def fix_settings():
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        return jsonify({"error": "No settings found"}), 404
        
    if not settings.activity_level:
        settings.activity_level = 'moderate'  # Set a default
        db.session.commit()
        
    return jsonify({"message": "Settings fixed", "activity_level": settings.activity_level}), 200

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
        return redirect(url_for('register'))
    
    # Get latest weight entry
    latest_weight = WeightEntry.query.filter_by(
        user_id=current_user.id
    ).order_by(WeightEntry.date.desc()).first()
    
    # Calculate time remaining
    months_remaining = None
    if settings.start_date and settings.goal_months:
        start_date = settings.start_date
        goal_date = start_date + timedelta(days=settings.goal_months * 30.44)
        days_remaining = (goal_date - datetime.utcnow()).days
        months_remaining = round(days_remaining / 30.44, 1)
    
    return render_template('settings.html',
                         active_tab='settings',
                         settings=settings,
                         latest_weight=latest_weight,
                         current_weight_kg=latest_weight.weight if latest_weight else settings.current_weight_kg,
                         target_weight_kg=settings.target_weight_kg,
                         months_remaining=months_remaining,
                         timedelta=timedelta)

@app.route('/update_settings', methods=['POST'])
@login_required
def update_settings():
    data = request.json
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    
    try:
        if not settings.start_date:
            settings.start_date = datetime.utcnow()
            
        # Update weight goals
        if data.get('target_weight'):
            settings.target_weight_kg = float(data['target_weight'])
            
        if data.get('goal_months'):
            settings.goal_months = int(data['goal_months'])

        # Update other settings
        if data.get('protein_ratio'):
            settings.protein_ratio = float(data['protein_ratio'])
        if data.get('activity_level'):
            settings.activity_level = data['activity_level']
            
        # Determine new direction based on current weight and new target
        weight_direction = determine_weight_direction(
            settings.current_weight_kg,
            settings.target_weight_kg
        )
            
        # Recalculate calories based on current weight and new goals
        new_calories = calculate_calorie_target(
            current_weight_kg=settings.current_weight_kg,
            settings=settings
        )

        settings.max_calories = new_calories
        db.session.commit()

        return jsonify({
            "message": "Settings updated successfully",
            "target_weight": settings.target_weight_kg,
            "goal_months": settings.goal_months,
            "new_calories": new_calories,
            "weight_direction": weight_direction
        }), 200
        
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