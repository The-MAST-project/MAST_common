@echo off

set top=C:\Users\mast\PycharmProjects\MAST_unit
set venv=%top%\venv
set PYTHONUNBUFFERED=1

cd %top%
%venv%\Scripts\activate.bat
%venv%\Scripts\python.exe %top%\src\app.py
