# Conjure Orchestrator - Node.js Backend
FROM node:20-slim

WORKDIR /app

COPY package*.json ./
# RUN npm install

COPY . .

EXPOSE 3000

CMD ["node", "src/index.js"]
