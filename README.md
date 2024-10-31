## Fitness and Nutrition Tracker

Simple Flask-based web app for tracking protein intake, calorie intake, and workouts. This application will be self-hosted on my Raspberry Pi.

### Features
* Daily protein goal calculation based on body weight
* Daily protein intake tracking with progress indicator
* Daily calorie tracking
* Integration with the [wger API](https://github.com/wger-project/wger) for nutritional information
* Save and quickly add commonly eaten meals

* Log workouts with customized exercises for different workout types, like Back Day or Leg Day
* Track exercises with weights, sets, and reps
* View workout history
* Daily workout status indicator

## Setup
1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Initialize the database: 
   ```bash
   export FLASK_APP=app.py
   flask db init
   flask db migrate
   flask db upgrade

4. Run the application: `python app.py`
