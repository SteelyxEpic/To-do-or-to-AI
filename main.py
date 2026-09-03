import os
from pydantic import BaseModel
import time
from datetime import date
from groq import Groq
from pydantic import BaseModel, ValidationError
import json

todolist = {}


api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    api_key = "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"

client = Groq(api_key=api_key)
def get_all_keys(d: dict):
    keys = []
    for key, value in d.items():
        keys.append(key)
        for val in value:
            if isinstance(val, dict):
                temp = get_all_keys(val)
                for i in temp:
                    keys.append(key + "/" + i)
    return keys

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
client = Groq(api_key=api_key)
text = ""


prints("System ready awaiting input")
while True:
        text = inputs("Hi! I'm your Todo list assistant. What would you like to do today? You can ask me to add, remove, or view your tasks.")

        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a todo list sorter. Today's date is {date.today()}."
                        " Extract the task details from the user's text and output a VALID JSON object matching this schema exactly:\n"
                        '{"Todo": "string", "Title": "string", "DueDate": "YYYY-MM-DD", "Time": "HH:MM", "Sort": "category/subcategory"}\n'
                        f"The 'Sort' field should be a string that represents the category and subcategory of the task, separated by a forward slash. If there is no subcategory, just provide the category. It can also have multiple subcategories, separated by forward slashes. Currently, the categories are: {str(get_all_keys(todolist))}, You can create new categories if you think it's necessary.\n"
                        " Do not return any pleasantries, introduction, or conversational filler. Return ONLY the raw JSON object."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Extract task details from: '{text}'",
                },
            ],
            response_format={"type": "json_object"},  # Forces Groq to output raw JSON
        )

        # 1. Grab the raw text string from the response
        raw_json_string = response.choices[0].message.content

        # 2. Parse the string into a standard Python dictionary
        parsed_dict = json.loads(raw_json_string)

        task_data = struture(**parsed_dict)

        print(task_data)
       
        sorting = task_data.Sort.split("/")
        length = len(sorting)
        temp = {"Todo": task_data.Todo, "Title": task_data.Title, "DueDate": task_data.DueDate, "Time": task_data.Time}
        for i in get_all_keys(todolist):
            if task_data.Sort == i:
                todolist[sorting[0]][sorting[1]].append(temp)
                break
        else:
            templist = [temp]
            for i in reversed(sorting[1:]):
                templist = {i: templist}
            todolist[sorting[0]] = templist

        prints("Task added successfully! Here is your updated todo list:")
        print(todolist)

        time.sleep(0.1)
