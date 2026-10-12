import { Link } from 'react-router-dom';
import type { Bug } from '../../types';

export default function BugLink({ bug, label, onClick }: { bug: Bug; label?: string; onClick?: (event: React.MouseEvent<HTMLAnchorElement>) => void }) {
  return <Link className="bug-link" to={`/bugs/${encodeURIComponent(bug.bug_id)}`} onClick={onClick}>{label || bug.title}</Link>;
}
