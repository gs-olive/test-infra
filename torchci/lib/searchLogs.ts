import { JobData } from "./types";

export async function getSearchRes(
  jobs: JobData[],
  query: string,
  setSearchRes: any
) {
  // Helper function to be called in useEffect to make sure the component
  // reloads when it should (hopefully).  The actual searching is done in
  // searchLogs
  if (query == "") {
    setSearchRes({
      results: new Map(),
      info: undefined,
    });
    return;
  }
  setSearchRes({
    results: new Map(),
    info: "Loading... (this might take a while)",
  });
  const searchRes = await searchLogs(
    jobs.map((job) => job.id!),
    query
  );
  setSearchRes({
    results: searchRes,
    info: undefined,
  });
}

async function searchLogs(
  jobIds: string[],
  query: string
): Promise<Map<string, [number[], string[]] | undefined>> {
  if (query == "") {
    return new Map();
  }
  const results: [string, [number[], string[]] | undefined][] =
    await Promise.all(
      jobIds.map(async (jobId) => {
        return [jobId, await searchLog(jobId, query)];
      })
    );
  return new Map(results);
}

async function searchLog(
  jobId: string,
  query: string
): Promise<[number[], string[]] | undefined> {
  // Search indiividual log
  try {
    const result = await fetch(
      `https://ossci-raw-job-status.s3.amazonaws.com/log/${jobId}`
    );
    if (result.status != 200) {
      return undefined;
    }
    const lineNumbers = [];
    const lineTexts = [];
    const threshold = 100;
    for (const [index, line] of (await result.text()).split("\n").entries()) {
      if (line.includes(query)) {
        lineNumbers.push(index + 1);
        lineTexts.push(
          line.length > 100 ? `${line.substring(0, 100)}...` : line
        );
        if (lineNumbers.length >= threshold) {
          break;
        }
      }
    }
    return [lineNumbers, lineTexts];
  } catch (error) {
    return undefined;
  }
}
