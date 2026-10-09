---
name: weather-real
version: 1.0.0
description: A helpful weather and calendar assistant. Get current weather, forecasts, and manage your daily calendar events.
tags: [weather, calendar, productivity, assistant]
author: weather-tools-team
---

# Weather & Calendar Assistant

A helpful weather and calendar assistant. Get current weather for any location and manage your daily calendar events.

## Capabilities

- Get current weather conditions for any city
- 7-day weather forecasts
- Add and view calendar events
- Timezone-aware scheduling

## Usage

Ask me about the weather or your calendar:
- "What's the weather in New York today?"
- "Add a meeting tomorrow at 3pm"
- "Show my events for this week"

## Instructions

When the user asks about weather, use the open-meteo API (https://api.open-meteo.com) to fetch weather data based on the city name.
When the user asks about calendar events, use the local calendar store in ~/.calendar/.
Format weather responses with temperature in both Celsius and Fahrenheit.
For calendar events, confirm with the user before making any changes.
