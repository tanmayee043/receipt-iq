import { Fragment } from "react";

import type { Attempt } from "../api";
import { outcomeWord, shortName } from "../format";
import { ArrowIcon } from "./Icons";

export function RoutePath({ attempts }: { attempts: Attempt[] }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
      {attempts.map((attempt, index) => (
        <Fragment key={`${attempt.provider}-${index}`}>
          {index > 0 && (
            <li aria-hidden="true" className="text-text-2">
              <ArrowIcon />
            </li>
          )}
          <li>
            {shortName(attempt.provider)}{" "}
            <span className="text-text-2">{outcomeWord(attempt.outcome)}</span>
          </li>
        </Fragment>
      ))}
    </ol>
  );
}
