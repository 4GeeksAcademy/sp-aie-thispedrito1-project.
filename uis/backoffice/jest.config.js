/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "jsdom",
  roots: ["<rootDir>/__tests__"],
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        tsconfig: {
          module: "commonjs",
          jsx: "react-jsx",
          esModuleInterop: true,
        },
      },
    ],
  },
  moduleNameMapper: {
    // El codigo compartido de src/ importa con extension .js (convencion ESM);
    // en tests lo resolvemos al archivo .ts de origen.
    "^(\\.{1,2}/.*)\\.js$": "$1",
  },
  // src/ tiene junto a cada .ts su artefacto compilado .js (ESM); priorizamos
  // el fuente .ts para que ts-jest lo transforme.
  moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json"],
};
