# -*- encoding: utf-8 -*-
import re
import mysql.connector
from flask import render_template, request, redirect, url_for, flash
from jinja2 import TemplateNotFound
from apps import get_db_connection
from apps.study_years import blueprint

# Helper - Extract current page name from request
def get_segment(request):
    try:
        segment = request.path.split('/')[-1]
        return segment if segment != '' else 'study_years'
    except:
        return None

@blueprint.route('/study_years')
def study_years():
    """Fetches all study_years and renders the page."""
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute('SELECT * FROM study_year')
    study_years = cursor.fetchall()
    cursor.close()
    connection.close()
    return render_template('study_years/list.html', study_years=study_years, segment='study_years')

@blueprint.route('/add_study_years', methods=['GET', 'POST'])
def add_study_years():
    if request.method == 'POST':
        year_name = request.form.get('year_name')
        level = request.form.get('level')

        if not year_name or not level:
            flash("Please fill out all required fields!", "warning")
        elif not re.match(r'^[0-9]+$', level):
            flash('Level must be a valid number!', "danger")
        else:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute('SELECT * FROM study_year WHERE year_name = %s AND level = %s', (year_name, level))
                if cursor.fetchone():
                    flash("Study year with this name and level already exists!", "warning")
                else:
                    cursor.execute('INSERT INTO study_year (year_name, level) VALUES (%s, %s)', (year_name, level))
                    connection.commit()
                    flash("Study year successfully added!", "success")
                    return redirect(url_for('study_years_blueprint.study_years'))
            except mysql.connector.Error as err:
                flash(f"Error: {err}", "danger")
            finally:
                cursor.close()
                connection.close()

    return render_template('study_years/add_study_year.html', segment='add_study_year')

@blueprint.route('/edit_study_year/<int:year_id>', methods=['GET', 'POST'])
def edit_study_year(year_id):
    if request.method == 'POST':
        year_name = request.form.get('year_name')
        level = request.form.get('level')

        if not year_name or not level:
            flash("Please fill out all required fields!", "warning")
            return redirect(url_for('study_years_blueprint.edit_study_year', year_id=year_id))

        if not re.match(r'^[0-9]+$', level):
            flash("Level must be a valid number!", "danger")
            return redirect(url_for('study_years_blueprint.edit_study_year', year_id=year_id))

        connection = get_db_connection()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT * FROM study_year WHERE year_name = %s AND level = %s AND year_id != %s", 
                           (year_name, level, year_id))
            if cursor.fetchone():
                flash("A study year with the same name and level already exists!", "warning")
            else:
                cursor.execute("UPDATE study_year SET year_name = %s, level = %s WHERE year_id = %s", 
                               (year_name, level, year_id))
                connection.commit()
                flash("Study year updated successfully!", "success")
        except Exception as e:
            flash(f"Error: {str(e)}", "danger")
        finally:
            cursor.close()
            connection.close()
        return redirect(url_for('study_years_blueprint.study_years'))

    # GET request
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT * FROM study_year WHERE year_id = %s", (year_id,))
    study_year = cursor.fetchone()
    cursor.close()
    connection.close()

    if study_year:
        return render_template('study_years/edit_study_year.html', study_year=study_year, segment='study_years')
    else:
        flash("Study year not found.", "danger")
        return redirect(url_for('study_years_blueprint.study_years'))

@blueprint.route('/delete_study_years/<int:year_id>')
def delete_study_years(year_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        # Corrected table name and ID field
        cursor.execute('DELETE FROM study_year WHERE year_id = %s', (year_id,))
        connection.commit()
        flash("Study year deleted successfully.", "success")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    finally:
        cursor.close()
        connection.close()
    return redirect(url_for('study_years_blueprint.study_years'))

@blueprint.route('/<template>')
def route_template(template):
    try:
        if not template.endswith('.html'):
            template += '.html'
        segment = get_segment(request)
        return render_template("study_years/" + template, segment=segment)
    except TemplateNotFound:
        return render_template('home/page-404.html'), 404
    except:
        return render_template('home/page-500.html'), 500