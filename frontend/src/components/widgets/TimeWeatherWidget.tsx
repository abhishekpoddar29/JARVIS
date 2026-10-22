import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Cloud,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSun,
  Droplets,
  Snowflake,
  Sun,
  Wind,
} from "lucide-react";

interface WeatherSnapshot {
  temperature: number;
  apparentTemperature: number;
  humidity: number;
  windSpeed: number;
  weatherCode: number;
  updatedAt: string;
}

function weatherDescription(code: number): string {
  if (code === 0) return "Clear sky";
  if ([1, 2].includes(code)) return "Partly cloudy";
  if (code === 3) return "Overcast";
  if ([45, 48].includes(code)) return "Fog";
  if ([51, 53, 55, 56, 57].includes(code)) return "Drizzle";
  if ([61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return "Rain";
  if ([71, 73, 75, 77, 85, 86].includes(code)) return "Snow";
  if ([95, 96, 99].includes(code)) return "Thunderstorm";
  return "Current conditions";
}

function WeatherIcon({ code }: { code: number }) {
  if (code === 0) return <Sun size={28} />;
  if ([1, 2].includes(code)) return <CloudSun size={28} />;
  if (code === 3) return <Cloud size={28} />;
  if ([45, 48].includes(code)) return <CloudFog size={28} />;
  if ([71, 73, 75, 77, 85, 86].includes(code)) {
    return <Snowflake size={28} />;
  }
  if ([95, 96, 99].includes(code)) {
    return <CloudLightning size={28} />;
  }
  return <CloudRain size={28} />;
}

export function TimeWeatherWidget() {
  const [now, setNow] = useState(() => new Date());
  const [weather, setWeather] = useState<WeatherSnapshot | null>(null);
  const [weatherError, setWeatherError] = useState<string | null>(null);

  const latitude = Number(
    import.meta.env.VITE_WEATHER_LAT ?? 12.9716,
  );
  const longitude = Number(
    import.meta.env.VITE_WEATHER_LON ?? 77.5946,
  );
  const locationLabel =
    import.meta.env.VITE_WEATHER_LABEL ?? "Bengaluru";

  const loadWeather = useCallback(async () => {
    const controller = new AbortController();
    const timeout = window.setTimeout(
      () => controller.abort(),
      8_000,
    );

    try {
      const params = new URLSearchParams({
        latitude: String(latitude),
        longitude: String(longitude),
        current:
          "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        timezone: "auto",
      });

      const response = await fetch(
        `https://api.open-meteo.com/v1/forecast?${params.toString()}`,
        { signal: controller.signal },
      );
      if (!response.ok) {
        throw new Error(`Weather request failed: ${response.status}`);
      }

      const data = await response.json();
      const current = data.current;
      if (!current) throw new Error("Weather response has no current data.");

      setWeather({
        temperature: Number(current.temperature_2m),
        apparentTemperature: Number(current.apparent_temperature),
        humidity: Number(current.relative_humidity_2m),
        windSpeed: Number(current.wind_speed_10m),
        weatherCode: Number(current.weather_code),
        updatedAt: String(current.time ?? new Date().toISOString()),
      });
      setWeatherError(null);
    } catch (error) {
      if (
        error instanceof DOMException &&
        error.name === "AbortError"
      ) {
        setWeatherError("Weather request timed out");
      } else {
        setWeatherError("Weather unavailable");
      }
    } finally {
      window.clearTimeout(timeout);
    }
  }, [latitude, longitude]);

  useEffect(() => {
    const clock = window.setInterval(
      () => setNow(new Date()),
      1_000,
    );
    return () => window.clearInterval(clock);
  }, []);

  useEffect(() => {
    void loadWeather();
    const refresh = window.setInterval(
      () => void loadWeather(),
      10 * 60 * 1_000,
    );
    return () => window.clearInterval(refresh);
  }, [loadWeather]);

  const formattedTime = useMemo(
    () =>
      new Intl.DateTimeFormat("en-IN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(now),
    [now],
  );

  const formattedDate = useMemo(
    () =>
      new Intl.DateTimeFormat("en-IN", {
        weekday: "long",
        day: "2-digit",
        month: "long",
        year: "numeric",
      }).format(now),
    [now],
  );

  return (
    <section className="hud-widget time-weather-widget">
      <div className="widget-corner widget-corner-tl" />
      <div className="widget-corner widget-corner-br" />

      <div className="time-block">
        <div className="widget-eyebrow">LOCAL SYSTEM TIME</div>
        <time className="system-time" dateTime={now.toISOString()}>
          {formattedTime}
        </time>
        <div className="system-date">{formattedDate}</div>
      </div>

      <div className="weather-divider" />

      {weather ? (
        <div className="weather-block">
          <div className="weather-primary">
            <div className="weather-icon">
              <WeatherIcon code={weather.weatherCode} />
            </div>
            <div>
              <div className="weather-temperature">
                {Math.round(weather.temperature)}°
              </div>
              <div className="weather-description">
                {weatherDescription(weather.weatherCode)}
              </div>
            </div>
          </div>

          <div className="weather-location">{locationLabel}</div>

          <div className="weather-metrics">
            <span>
              <Droplets size={12} /> {Math.round(weather.humidity)}%
            </span>
            <span>
              <Wind size={12} /> {Math.round(weather.windSpeed)} km/h
            </span>
            <span>Feels {Math.round(weather.apparentTemperature)}°</span>
          </div>
        </div>
      ) : (
        <div className="weather-loading">
          <Cloud size={22} />
          <span>{weatherError ?? "Loading weather…"}</span>
        </div>
      )}
    </section>
  );
}