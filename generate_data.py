import csv
import random

# Realistic data pools
titles = ["The Dark Knight", "Inception", "Interstellar", "Pulp Fiction", "Forrest Gump", 
          "Star Wars: A New Hope", "Avatar", "The Matrix", "Titanic", "Jurassic Park", 
          "The Lion King", "The Godfather", "Fight Club", "The Lord of the Rings", 
          "Schindler's List", "Spirited Away", "Parasite", "The Departed", "Gladiator", "Whiplash"]

years = list(range(1990, 2024))
certificates = ["R", "PG-13", "PG", "G", "NC-17", "Not Rated"]
genres = ["Action, Adventure, Sci-Fi", "Drama, Crime", "Comedy, Romance", "Biography, Drama, History", 
          "Horror, Mystery, Thriller", "Animation, Family", "Action, Thriller", "Drama, War"]
directors = ["Christopher Nolan, Zack Snyder", "Steven Spielberg", "Quentin Tarantino", 
             "Martin Scorsese", "James Cameron", "Peter Jackson, George Lucas", 
             "Ridley Scott", "Denis Villeneuve", "David Fincher"]

# Generate 2000 rows
with open('imdb_movies.csv', mode='w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['Title', 'Year', 'Certificate', 'Genre', 'Director', 'Votes', 'Revenue'])
    
    for i in range(2000):
        title = f"{random.choice(titles)}: Part {i+1}"
        year = random.choice(years)
        cert = random.choice(certificates)
        genre = random.choice(genres)
        director = random.choice(directors)
        votes = random.randint(10000, 2500000)
        # Revenue in millions (e.g., 760.51)
        revenue = round(random.uniform(1.5, 950.8), 2) 
        
        writer.writerow([title, year, cert, genre, director, votes, revenue])

print("✅ Successfully generated 'imdb_movies.csv' with 2000 rows!")