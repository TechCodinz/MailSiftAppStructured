#!/bin/bash

# MailSift Ultra Startup Script
# This script sets up and runs the MailSift Ultra application

set -e

echo "🚀 Starting MailSift Ultra..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "📝 Please edit .env file with your configuration"
    exit 1
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Check Python version
python_version=$(python3 --version 2>&1 | grep -Po '(?<=Python )\d+\.\d+')
required_version="3.11"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then 
    echo "❌ Python $required_version or higher is required. Current version: $python_version"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/upgrade pip
pip install --upgrade pip

# Install dependencies
echo "📚 Installing dependencies..."
pip install -r requirements.txt

# Check Redis connection
echo "🔍 Checking Redis connection..."
if command -v redis-cli &> /dev/null; then
    redis-cli ping > /dev/null 2>&1 || {
        echo "⚠️  Redis is not running. Starting Redis in Docker..."
        docker run -d --name redis -p 6379:6379 redis:alpine
    }
else
    echo "⚠️  Redis CLI not found. Make sure Redis is running."
fi

# Check PostgreSQL connection (optional)
if [ ! -z "$DATABASE_URL" ]; then
    echo "🔍 Checking PostgreSQL connection..."
    # Add database check logic here if needed
fi

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p data logs

# Run database migrations if alembic is configured
if [ -d "alembic" ]; then
    echo "🗄️  Running database migrations..."
    alembic upgrade head
fi

# Start the application
echo "✨ Starting MailSift Ultra server..."
echo "📍 Access the application at: http://localhost:5000"
echo "📊 API documentation at: http://localhost:5000/api/docs"
echo "🎯 Admin dashboard at: http://localhost:5000/admin/dashboard"
echo ""
echo "Press Ctrl+C to stop the server"

# Run the server
if [ "$FLASK_ENV" = "production" ]; then
    echo "🏭 Running in production mode with Gunicorn..."
    gunicorn --bind 0.0.0.0:5000 \
             --workers 4 \
             --threads 2 \
             --timeout 120 \
             --access-logfile logs/access.log \
             --error-logfile logs/error.log \
             server_v2:app
else
    echo "🔧 Running in development mode..."
    python server_v2.py
fi