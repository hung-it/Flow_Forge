FROM node:24.11.0-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY --chown=node:node . .
RUN chown -R node:node /app
USER node
CMD ["npm", "run", "dev"]
