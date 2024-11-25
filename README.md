## Fitness and Nutrition Tracker

Simple Flask-based web app for tracking protein intake, calorie intake, and workouts. This application will be self-hosted on my Raspberry Pi.

### Features

#### Nutrition
+ Daily protein goal calculation based on body weight
+ Daily protein intake tracking with progress indicator
+ Daily calorie tracking
+ Integration with Claude for using Claude to estimate nutrition content of meals quickly and logging those meals
+ Save and quickly add commonly eaten meals

#### Workouts
+ Log workouts with weights, set, reps for each exericse
+ Customizable workout categories and exercises
+ Track exercises with weights, sets, and reps
+ View workout history
+ Daily workout status indicator
+ Track exercise progress over time

### Prerequisites
- Python 3.8 or higher
- SQLite3
- Anthropic API key (for Claude integration)

### Setup
1. Clone the repository
2. Create an activate virtual environment
   '''bash
   python -m venv venv
   source venv/bin/activate  
3. Install dependencies: `pip install -r requirements.txt`
4. Set up env variables
3. Initialize the database: 
   ```bash
   export FLASK_APP=app.py
   flask db init
   flask db migrate
   flask db upgrade
4. Run the application: `python app.py`

### Contributing

This is a personal project for my own usage, but feel free to suggest improvements, provide improvements yourself, or fork and modify for your own use. 