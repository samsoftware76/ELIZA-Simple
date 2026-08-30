# ELIZA Project and Client Management App

This is a Flask-based web application for managing clients, projects, and tasks.
It is designed to be deployed on Vercel.

## Key Features (Planned)
- Client Information Management
- Project Tracking
- Task Management (To-Do style)
- User Roles and Permissions
- Email Notifications
- Secure Data Handling

## Tech Stack
- Python (Flask)
- PostgreSQL (Neon DB - using `psycopg2-binary` and `Flask-SQLAlchemy`)
- Vercel (Hosting)
- `python-dotenv` for managing environment variables locally

## Project Structure
- `ELIZA_App/` (Root project folder)
  - `api/index.py`: Main Flask application and Vercel entry point.
  - `models/`: Directory for SQLAlchemy database models.
  - `services/`: Directory for business logic services.
  - `static/`: For static files (CSS, JavaScript, images).
  - `templates/`: For HTML templates.
  - `vercel.json`: Vercel deployment configuration.
  - `requirements.txt`: Python dependencies.
  - `.env.example`: Example for environment variables (especially `DATABASE_URL`).
  - `.env`: (You create this) For local environment variables like `DATABASE_URL`.
  - `venv/`: (You create this) Python virtual environment directory.

## Setup for Local Development

1.  **Navigate to the Project Directory**:
    Open your terminal or command prompt and change to the `ELIZA_App` directory:
    ```bash
    cd path\to\company book\ELIZA_App
    ```

2.  **Create and Activate a Virtual Environment** (Highly Recommended):
    ```bash
    # Create the virtual environment (only need to do this once)
    python -m venv venv

    # Activate it (do this every time you work on the project)
    # Windows (PowerShell):
    .\venv\Scripts\Activate.ps1
    # Windows (CMD):
    # venv\Scripts\activate.bat
    # Linux/macOS (bash/zsh):
    # source venv/bin/activate
    ```

3.  **Install Dependencies**:
    With your virtual environment activated, install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set Up Environment Variables (for local development)**:
    *   In the `ELIZA_App` directory (where `requirements.txt` is), create a new file named exactly `.env` (no `.txt` extension).
    *   Open the `.env` file and add your Neon database connection string. It should look like this:
        ```env
        DATABASE_URL='postgresql://neondb_owner:YOUR_PASSWORD@ep-summer-boat-a5i34m4w-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require'
        ```
        Replace `YOUR_PASSWORD` with the actual password for `neondb_owner` you were given.
        **Important**: Do not commit the `.env` file to Git if you are using version control, especially for public repositories. Add `.env` to your `.gitignore` file.

5.  **Run the Flask Development Server**:
    Make sure your virtual environment is still active and you are in the `ELIZA_App` directory.
    ```bash
    python api/index.py
    ```
    You should see output indicating the server is running, typically on `http://127.0.0.1:5000/`. The console will also show if the `.env` file was loaded and if the `DATABASE_URL` was found.

6.  **View in Browser**:
    Open your web browser and go to `http://127.0.0.1:5000/`.
    The page should load and indicate whether the database connection string is configured.

## Deployment to Vercel

-   Connect your Git repository (GitHub, GitLab, Bitbucket) to Vercel.
-   Ensure your `DATABASE_URL` (with the correct password) is set as an Environment Variable in your Vercel project settings.
-   Vercel will use `vercel.json` (for build and routing configuration) and `api/index.py` (as the serverless function entry point) to build and deploy your application.
-   Static files in the `static` directory will be served automatically by Vercel based on the `vercel.json` configuration.
