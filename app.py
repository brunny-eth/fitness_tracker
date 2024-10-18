from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import requests

# setup for flask app + db's
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fitness_tracker.db'
db = SQLAlchemy(app)

# db models
class ProteinEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Float, nullable=False)

class WorkoutEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    exercise = db.Column(db.String(100), nullable=False)
    weight = db.Column(db.Float)
    reps = db.Column(db.Integer)
    duration = db.Column(db.Integer)

# helper functions
def get_protein_from_wger(ingredient_name):
    url = "https://wger.de/api/v2/ingredient/"
    params = {"name": ingredient_name}
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        if data['results']:
            return data['results'][0]['protein']
    return None

# routes
@app.route('/')
def home():
    return '''
    <h1>Fitness Tracker</h1>
    <h2>Add Protein Entry</h2>
    <form id="proteinForm">
        <input type="text" id="ingredient" placeholder="Ingredient name">
        <input type="number" id="amount" placeholder="Or enter amount directly">
        <button type="submit">Add</button>
    </form>
    <h2>Add Workout Entry</h2>
    <form id="workoutForm">
        <input type="text" id="exercise" placeholder="Exercise name" required>
        <input type="number" id="weight" placeholder="Weight (lbs)">
        <input type="number" id="reps" placeholder="Reps">
        <input type="number" id="duration" placeholder="Duration (minutes)">
        <button type="submit">Add</button>
    </form>
    <script>
        document.getElementById('proteinForm').onsubmit = function(e) {
            e.preventDefault();
            fetch('/add_protein', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ingredient: document.getElementById('ingredient').value,
                    amount: document.getElementById('amount').value
                })
            }).then(response => response.json()).then(data => alert(JSON.stringify(data)));
        };
        document.getElementById('workoutForm').onsubmit = function(e) {
            e.preventDefault();
            fetch('/add_workout', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    exercise: document.getElementById('exercise').value,
                    weight: document.getElementById('weight').value,
                    reps: document.getElementById('reps').value,
                    duration: document.getElementById('duration').value
                })
            }).then(response => response.json()).then(data => alert(JSON.stringify(data)));
        };
    </script>
    '''

@app.route('/add_protein', methods=['POST'])
def add_protein():
    data = request.json
    ingredient_name = data.get('ingredient')
    if ingredient_name:
        protein_amount = get_protein_from_wger(ingredient_name)
        if protein_amount is None:
            return jsonify({"error": "Ingredient not found"}), 404
    else:
        protein_amount = data.get('amount')
    
    if protein_amount is None:
        return jsonify({"error": "Protein amount not provided"}), 400
    
    new_entry = ProteinEntry(date=datetime.now().date(), amount=protein_amount)
    db.session.add(new_entry)
    db.session.commit()
    return jsonify({"message": "Protein entry added successfully", "amount": protein_amount}), 201

@app.route('/protein_entries', methods=['GET'])
def get_protein_entries():
    entries = ProteinEntry.query.all()
    return jsonify([
        {"date": entry.date.isoformat(), "amount": entry.amount}
        for entry in entries
    ])

@app.route('/add_workout', methods=['POST'])
def add_workout():
    data = request.json
    new_entry = WorkoutEntry(
        date=datetime.now().date(),
        exercise=data['exercise'],
        weight=data.get('weight'),
        reps=data.get('reps'),
        duration=data.get('duration')
    )
    db.session.add(new_entry)
    db.session.commit()
    return jsonify({"message": "Workout entry added successfully"}), 201

@app.route('/workout_entries', methods=['GET'])
def get_workout_entries():
    entries = WorkoutEntry.query.all()
    return jsonify([
        {
            "date": entry.date.isoformat(),
            "exercise": entry.exercise,
            "weight": entry.weight,
            "reps": entry.reps,
            "duration": entry.duration
        }
        for entry in entries
    ])

@app.route('/compare_workouts', methods=['GET'])
def compare_workouts():
    exercise = request.args.get('exercise')
    date = datetime.strptime(request.args.get('date'), '%Y-%m-%d').date()
    
    this_week = WorkoutEntry.query.filter_by(exercise=exercise, date=date).first()
    last_week = WorkoutEntry.query.filter_by(exercise=exercise, date=date-timedelta(days=7)).first()
    
    if this_week and last_week:
        comparison = {
            "this_week": {
                "weight": this_week.weight,
                "reps": this_week.reps,
                "duration": this_week.duration
            },
            "last_week": {
                "weight": last_week.weight,
                "reps": last_week.reps,
                "duration": last_week.duration
            }
        }
        return jsonify(comparison)
    else:
        return jsonify({"message": "Not enough data for comparison"}), 404

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)