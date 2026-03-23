from nicegui import ui

columns = [
    {'name': 'name', 'label': 'Name', 'field': 'name', 'sortable': True},
    {'name': 'age', 'label': 'Age', 'field': 'age', 'sortable': True},
]

rows = [
    {'name': 'Alice', 'age': 25},
    {'name': 'Bob', 'age': 30},
]

ui.table(columns=columns, rows=rows, row_key='name') \
    .props('dense bordered flat')

ui.run()