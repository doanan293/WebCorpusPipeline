# Lucene: lap chi muc tu MongoDB, dong bo tang dan, API tim kiem va doc du lieu. Build context la lucene/.
FROM maven:3.9-eclipse-temurin-17 AS build
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pom.xml synonyms.txt ./
COPY scripts scripts
# VnCoreNLP va thu vien truoc ma nguon, de sua code khong phai tai lai.
RUN bash scripts/setup-vncorenlp.sh && mvn -q -B dependency:go-offline
COPY src src
RUN mvn -q -B -o package

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /src/target/lucene-search.jar lucene-search.jar
COPY --from=build /src/lib lib
COPY --from=build /src/models models
COPY --from=build /src/synonyms.txt synonyms.txt
COPY --from=build /src/scripts/docker-entrypoint.sh docker-entrypoint.sh
ENTRYPOINT ["bash", "docker-entrypoint.sh"]
