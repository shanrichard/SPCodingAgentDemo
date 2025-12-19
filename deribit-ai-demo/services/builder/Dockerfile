# Use Playwright official image (includes Chromium and all deps)
FROM mcr.microsoft.com/playwright:v1.57.0-jammy

# Install Node 22 (Playwright image uses older Node)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs

# python + git
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# pnpm + claude-code
RUN npm install -g pnpm @anthropic-ai/claude-code

# Create non-root user with same UID as host user for volume permissions
RUN useradd -m -s /bin/bash -u 2026 builder

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY builder ./builder
COPY template ./template

# Make template readable
RUN chown -R builder:builder /app

ENV PYTHONUNBUFFERED=1

# Switch to non-root user
USER builder

CMD ["uvicorn", "builder.main:app", "--host", "0.0.0.0", "--port", "8090"]
