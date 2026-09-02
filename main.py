from google import genai
from pydantic import BaseModel
import time
from datetime import date

def inputs(string:str = ""):
    for x in range(0, len(string)):
        print(string[x], end = "", flush=True)
        if string[x] == "." or string[x] == "!" or string[x] == "?":
            time.sleep(0.2)
        else:
            time.sleep(0.01)
    print()
    return input(">")
    print()
    
def prints(string:str = ""):
    for x in range(0, len(string)):
        print(string[x], end = "", flush=True)
        if string[x] == "." or string[x] == "!" or string[x] == "?":
            time.sleep(0.2)
        else:
            time.sleep(0.01)
    print()

prints("please wait a moment...")



class struture (BaseModel):
    Todo: str
    Title: str
    DueDate: str
    Time: str
    Sort: str
client = genai.Client(api_key="AIzaSyCRJ3taHFP_71x24IBnn4WtpoGUaVRdHfo")
text = ""


prints("System ready awaiting input")
while True:
        text = inputs("Hi! I'm your Todo list assistant. What would you like to do today? You can ask me to add, remove, or view your tasks.")

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents="You are a todo list sorter and today's date is " + str(date.today()) + ", a person has added a task with this description: " + text + " Please return a JSON array of objects with the following fields: Todo, Title, DueDate(in the format of YYYY-MM-DD), Time(in the format of HH:MM), Sort(The sort can be attributed like 'workrelated/jannet'). Each object should represent a task. If the user input does not contain any tasks, return an empty array.",
            config={
                "response_mime_type": "application/json",
                "response_schema": list[struture],
            },
        )

        my_recipes: list[struture] = response.parsed
        print(my_recipes[0])
       
        sorting = my_recipes[0].Sort.split("/")
        print(sorting)
        
        time.sleep(0.1)
        
                
        print(my_recipes[0]) 
        prints("awaiting input")

