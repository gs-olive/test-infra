import getRocksetClient from "lib/rockset";
import { NextApiRequest, NextApiResponse } from "next";

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  const body = JSON.parse(req.body);
  const timestamp = Date.now();

  if (req.method == "POST" && body.url.startsWith("https://hud.pytorch.org")) {
    const rocksetClient = getRocksetClient();
    rocksetClient.documents.addDocuments("commons", "hud_tracking", {
      data: [{ ...body, timestamp: timestamp }],
    });
  }

  res.status(200).end();
}
